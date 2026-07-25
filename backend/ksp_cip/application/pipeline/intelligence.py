"""Post-load intelligence refresh — the gold layer.

Runs after every successful load, in dependency order:

    entity resolution → graph edges → centrality → offender scores
                     ↘ embeddings index
                     ↘ hotspot cells → early-warning alerts → case priority

Each stage is idempotent and writes a watermark, so a Catalyst Circuit can
schedule them individually without the local behaviour diverging.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from ...domain.ports import Clock
from ...infrastructure.db.repositories import (
    AlertRepository,
    AnalyticsRepository,
    CaseRepository,
    ControlRepository,
    GraphRepository,
    HotspotRepository,
    IdentityRepository,
    PriorityRepository,
    ReferenceRepository,
)
from ...infrastructure.db.repositories.analytics import AggregateFilter
from ...domain.models import UnitScope
from ...infrastructure.observability import get_logger
from ..analytics import AnalyticsEngine
from ..graph import EntityResolver, GraphBuilder, GraphService, person_node, score_offenders
from ..rag import RetrievalService

LOGGER = get_logger(__name__)
STATEWIDE = UnitScope(statewide=True)


@dataclass(slots=True)
class RefreshReport:
    identities: int = 0
    review_links: int = 0
    auto_links: int = 0
    edges: int = 0
    offender_scores: int = 0
    embedding_documents: int = 0
    hotspot_cells: int = 0
    alerts: int = 0
    priority_scores: int = 0
    timings_ms: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "identities": self.identities, "review_links": self.review_links,
            "auto_links": self.auto_links, "edges": self.edges,
            "offender_scores": self.offender_scores,
            "embedding_documents": self.embedding_documents,
            "hotspot_cells": self.hotspot_cells, "alerts": self.alerts,
            "priority_scores": self.priority_scores, "timings_ms": self.timings_ms,
        }


class IntelligenceRefresher:
    def __init__(
        self,
        *,
        cases: CaseRepository,
        reference: ReferenceRepository,
        analytics: AnalyticsRepository,
        graph_repository: GraphRepository,
        graph_service: GraphService,
        identities: IdentityRepository,
        hotspots: HotspotRepository,
        alerts: AlertRepository,
        priority: PriorityRepository,
        control: ControlRepository,
        retrieval: RetrievalService,
        engine: AnalyticsEngine,
        resolver: EntityResolver,
        builder: GraphBuilder,
        clock: Clock,
    ) -> None:
        self._cases = cases
        self._reference = reference
        self._analytics = analytics
        self._graph_repository = graph_repository
        self._graph_service = graph_service
        self._identities = identities
        self._hotspots = hotspots
        self._alerts = alerts
        self._priority = priority
        self._control = control
        self._retrieval = retrieval
        self._engine = engine
        self._resolver = resolver
        self._builder = builder
        self._clock = clock

    def refresh_all(self, *, transactions: list[dict[str, Any]] | None = None) -> RefreshReport:
        report = RefreshReport()
        as_of = self._clock.now().date()

        self._reference.invalidate()
        closure_rows = self._reference.rebuild_unit_closure()
        LOGGER.info("unit_closure_rebuilt", extra={"rows": closure_rows})

        identities, records_by_accused = self._resolve_identities(report)
        self._build_graph(report, identities, records_by_accused, transactions or [])
        self._score_offenders(report, identities, records_by_accused, as_of)
        self._refresh_embeddings(report)
        self._refresh_hotspots(report, as_of)
        self._refresh_alerts(report, as_of)
        self._refresh_priority(report, as_of)

        self._control.set_watermark("intelligence_refresh", datetime.now(timezone.utc).isoformat(),
                                    report.as_dict())
        return report

    # ------------------------------------------------------------- stages
    def _resolve_identities(self, report: RefreshReport) -> tuple[list[Any], dict[int, Any]]:
        rows = self._cases.all_accused()
        records = self._resolver.to_records(rows)
        links, identities = self._resolver.resolve(records)
        records_by_accused = {record.accused_master_id: record for record in records}

        self._identities.replace_identities([
            {
                "identity_id": identity.identity_id,
                "canonical_name": identity.canonical_name,
                "normalized_name": identity.normalized_name,
                "phonetic_key": identity.phonetic_key,
                "age_estimate": identity.age_estimate,
                "gender_id": identity.gender_id,
                "district_ids": identity.district_ids,
                "unit_ids": identity.unit_ids,
                "source_ids": identity.source_ids,
                "case_ids": identity.case_ids,
                "crime_nos": identity.crime_nos,
            }
            for identity in identities
        ])
        self._identities.replace_links([
            {
                "link_id": link.link_id,
                "left_accused_id": link.left_accused_id,
                "right_accused_id": link.right_accused_id,
                "score": link.score,
                "decision": link.decision,
                "features": link.features,
                "state": "pending" if link.decision == "review" else "auto",
            }
            for link in links
        ])
        report.identities = len(identities)
        report.auto_links = sum(1 for link in links if link.decision == "auto_link")
        report.review_links = sum(1 for link in links if link.decision == "review")
        LOGGER.info("entity_resolution_complete", extra={
            "identities": report.identities, "auto": report.auto_links, "review": report.review_links,
        })
        return identities, records_by_accused

    def _build_graph(
        self, report: RefreshReport, identities: list[Any], records_by_accused: dict[int, Any],
        transactions: list[dict[str, Any]],
    ) -> None:
        cases = self._cases.cases_for_graph_build()
        arrests = self._cases.all_arrests()
        edges = self._builder.build(
            identities=identities,
            accused_records=list(records_by_accused.values()),
            cases=cases,
            arrests=arrests,
            transactions=transactions,
        )
        report.edges = self._graph_repository.replace_all(edges)
        self._graph_service.invalidate()
        labels = {person_node(identity.identity_id): identity.canonical_name for identity in identities}
        for row in cases:
            labels[f"case:{int(row['CaseMasterID'])}"] = f"FIR {row['CrimeNo']}"
        self._graph_service.set_labels(labels)
        LOGGER.info("graph_built", extra={"edges": report.edges})

    def _score_offenders(
        self, report: RefreshReport, identities: list[Any], records_by_accused: dict[int, Any], as_of: date
    ) -> None:
        centrality = self._graph_service.centrality(node_prefix="person:")
        scores = score_offenders(identities, records_by_accused, centrality=centrality, as_of=as_of)
        report.offender_scores = self._identities.replace_offender_scores(scores)

    def _refresh_embeddings(self, report: RefreshReport) -> None:
        result = self._retrieval.rebuild()
        report.embedding_documents = int(result["documents"])
        LOGGER.info("embeddings_rebuilt", extra=result)

    def _refresh_hotspots(self, report: RefreshReport, as_of: date) -> None:
        result = self._engine.hotspots(AggregateFilter(), STATEWIDE, window_days=90, as_of=as_of)
        report.hotspot_cells = self._hotspots.replace_cells(
            [asdict(cell) for cell in result.cells]
        )

    def _refresh_alerts(self, report: RefreshReport, as_of: date) -> None:
        alerts = self._engine.early_warning(STATEWIDE, as_of=as_of)
        report.alerts = self._alerts.replace_alerts([asdict(alert) for alert in alerts])

    def _refresh_priority(self, report: RefreshReport, as_of: date) -> None:
        from ...infrastructure.db.repositories import CaseFilter

        open_cases = self._cases.search(CaseFilter(limit=5000), STATEWIDE)
        accused_counts: dict[int, int] = {}
        for row in self._cases.all_accused():
            case_id = int(row["CaseMasterID"])
            accused_counts[case_id] = accused_counts.get(case_id, 0) + 1
        alert_case_ids = {
            int(case_id)
            for alert in self._alerts.alerts(limit=500)
            for case_id in alert.get("case_ids", [])
        }
        offender_by_case: dict[int, float] = {}
        for record in self._identities.top_offenders(limit=500):
            for case_id in record.get("case_ids", []):
                offender_by_case[int(case_id)] = max(
                    offender_by_case.get(int(case_id), 0.0), float(record["score"])
                )
        arrest_counts: dict[int, int] = {}
        for row in self._cases.all_arrests():
            case_id = int(row["CaseMasterID"])
            arrest_counts[case_id] = arrest_counts.get(case_id, 0) + 1
        chargesheeted = {int(row["CaseMasterID"]) for row in self._cases.all_chargesheets()}
        network_sizes: dict[int, int] = {}
        graph = self._graph_service.graph
        for case in open_cases:
            node = f"case:{case.case_master_id}"
            if node in graph:
                network_sizes[case.case_master_id] = graph.degree(node)

        scores = []
        for case in open_cases:
            computed = self._engine.priority_for_summary(
                case,
                accused_count=accused_counts.get(case.case_master_id, 0),
                as_of=as_of,
                arrest_count=arrest_counts.get(case.case_master_id, 0),
                chargesheet_filed=case.case_master_id in chargesheeted,
                network_size=network_sizes.get(case.case_master_id, 0),
                max_offender_score=offender_by_case.get(case.case_master_id, 0.0),
                alert_linked=case.case_master_id in alert_case_ids,
            )
            if computed is None:
                continue
            scores.append({
                "case_master_id": case.case_master_id,
                "crime_no": case.crime_no,
                "score": computed["score"],
                "band": computed["band"],
                "components": computed["components"],
            })
        report.priority_scores = self._priority.replace_scores(scores)
