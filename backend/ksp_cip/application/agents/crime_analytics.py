"""CrimeAnalyticsAgent — trends, hotspots, early warning, sociological insight.

Every figure this agent reports comes from :class:`AnalyticsEngine`, which is
pure arithmetic over ``GROUP BY`` results. The agent's job is to turn those
numbers into evidence-bound claims and a chart specification, and to attach
the caveats that make the numbers safe to act on.
"""

from __future__ import annotations

from typing import Any

from ...domain.enums import AgentName, Intent, Permission, Provenance
from ...domain.models import AgentResult, StructuredPayload
from ...infrastructure.db.repositories import (
    AggregateFilter,
    AlertRepository,
    AnalyticsRepository,
    HotspotRepository,
    ReferenceRepository,
)
from ..analytics import AnalyticsEngine
from ..nlu import default_date_window
from ..services.audit import AuditService, audited
from ..services.authorization import AuthorizationService
from ..services.evidence import aggregate_evidence, alert_evidence, claim, empty_result_evidence, trace
from .base import AgentRequest, BaseAgent

SOCIOLOGY_DIMENSIONS = ("occupation", "age_band", "gender", "religion", "caste")
#: The organiser's Victim table has no occupation/religion/caste columns —
#: a subject-selector request for those against victims is honestly
#: substituted, not silently dropped or rejected.
VICTIM_DIMENSIONS = ("gender", "age_band")
SEASONAL_LOOKBACK_MONTHS = 48


class CrimeAnalyticsAgent(BaseAgent):
    name = AgentName.CRIME_ANALYTICS

    def __init__(
        self,
        audit: AuditService,
        engine: AnalyticsEngine,
        analytics: AnalyticsRepository,
        reference: ReferenceRepository,
        hotspots: HotspotRepository,
        alerts: AlertRepository,
        authorization: AuthorizationService,
    ) -> None:
        super().__init__(audit)
        self._engine = engine
        self._analytics = analytics
        self._reference = reference
        self._hotspots = hotspots
        self._alerts = alerts
        self._authorization = authorization

    @audited("agent.crime_analytics", object_type="aggregate")
    def handle(self, request: AgentRequest) -> AgentResult:
        request.principal.require(Permission.READ_AGGREGATES)
        if request.intent is Intent.HOTSPOT_QUERY:
            return self._hotspot(request)
        if request.intent is Intent.EARLY_WARNING:
            return self._early_warning(request)
        if request.intent is Intent.DEMOGRAPHIC_INSIGHT:
            return self._sociology(request)
        if request.intent is Intent.SEASONAL_QUERY:
            return self._seasonal(request)
        return self._trend(request)

    # -------------------------------------------------------------- trends
    def _trend(self, request: AgentRequest) -> AgentResult:
        start, end = default_date_window(request.slots, today=request.today, months=12)
        filters = AggregateFilter(
            unit_ids=request.slots.unit_ids or None,
            district_ids=request.slots.district_ids or None,
            crime_sub_head_ids=request.slots.crime_sub_head_ids or None,
            date_from=start,
            date_to=end,
        )
        result = self._engine.trend(filters, request.scope)
        if result.total == 0:
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim("No registered cases fall inside that filter, so there is no series to report.")],
                traces=[result.trace], confidence=0.9,
            )

        scope_label = self._scope_label(request)
        evidence = aggregate_evidence(
            key=f"trend:{start}:{end}:{'-'.join(map(str, request.slots.district_ids)) or 'all'}",
            label=f"{result.total} FIRs counted by month, {start} to {end}, {scope_label}",
            case_master_ids=result.case_ids,
            crime_nos=result.crime_nos,
            detail={"periods": result.periods, "counts": result.counts},
        )
        claims = [
            claim(
                f"{result.total} case(s) were registered {scope_label} between {start} and {end}.",
                [evidence], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ),
            claim(
                f"The monthly series is {result.direction} at {result.slope_per_month:+.2f} cases per month.",
                [evidence], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ),
        ]
        if result.latest_period:
            change = (
                f"{result.change_percent:+.1f}% against the previous month"
                if result.change_percent is not None else "no comparable previous month"
            )
            claims.append(claim(
                f"The most recent month, {result.latest_period}, recorded {result.latest_count} case(s) — {change}.",
                [evidence], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        if result.year_on_year_percent is not None:
            claims.append(claim(
                f"Year on year, that month is {result.year_on_year_percent:+.1f}% against the same month a year earlier.",
                [evidence], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        if result.breakdown:
            top = result.breakdown[0]
            claims.append(claim(
                f"The largest single category is {top['sub_head']} with {top['case_count']} case(s).",
                [evidence], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        claims.append(claim(
            "Counts are of FIRs registered, which reflects reporting and registration practice as well as "
            "underlying crime."
        ))

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=[evidence],
            traces=[result.trace],
            payload=StructuredPayload(
                payload_type="line",
                title=f"Monthly registered cases — {scope_label}",
                data={
                    "labels": result.periods,
                    "series": [{"name": "Registered cases", "values": result.counts}],
                    "breakdown": result.breakdown,
                },
            ),
            data={"case_master_ids": result.case_ids[:200], "trend": {
                "periods": result.periods, "counts": result.counts, "slope": result.slope_per_month}},
        )

    # ------------------------------------------------------------ hotspots
    def _hotspot(self, request: AgentRequest) -> AgentResult:
        window_days = request.slots.relative_period_days or 90
        filters = AggregateFilter(
            unit_ids=request.slots.unit_ids or None,
            district_ids=request.slots.district_ids or None,
            crime_sub_head_ids=request.slots.crime_sub_head_ids or None,
            date_from=request.slots.date_from,
            date_to=request.slots.date_to,
        )
        result = self._engine.hotspots(filters, request.scope, window_days=window_days, as_of=request.today)
        if not result.cells:
            nothing = empty_result_evidence(
                key=f"hotspot:none:{window_days}",
                label=f"No grid cell met the threshold in the last {window_days} days",
                detail={"window_days": window_days, "grid_metres": result.grid_metres,
                        "min_cases": self._engine.hotspot_min_cases},
            )
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(
                    f"No grid cell reached the reporting threshold in the last {window_days} days, so there is "
                    "no concentration worth flagging.",
                    [nothing], provenance=Provenance.DETERMINISTIC_COMPUTATION,
                )],
                evidence=[nothing], traces=[result.trace], confidence=0.85,
            )

        evidence_items = []
        claims = [claim(
            f"{len(result.cells)} location cluster(s) cleared the threshold across "
            f"{result.total_cases_considered} geocoded case(s) in the last {result.window_days} days.",
            provenance=Provenance.DETERMINISTIC_COMPUTATION,
        )]
        for index, cell in enumerate(result.cells[:5], start=1):
            item = aggregate_evidence(
                key=f"hotspot:{cell.cell_id}",
                label=f"Grid cell {cell.cell_id}: {cell.case_count} cases",
                case_master_ids=cell.case_ids,
                detail={"centroid": [cell.centroid_lat, cell.centroid_lon], "intensity": cell.intensity},
            )
            evidence_items.append(item)
            district = self._reference.district(cell.district_id) if cell.district_id else None
            claims.append(claim(
                f"Cluster {index}: {cell.case_count} case(s) around "
                f"{cell.centroid_lat:.4f}, {cell.centroid_lon:.4f}"
                + (f" in {district['DistrictName']}" if district else "")
                + f", {cell.intensity:.1f}× the average occupied cell"
                + (f", mostly {cell.top_crime_sub_head}" if cell.top_crime_sub_head else "")
                + ".",
                [item], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        claims[0].evidence_locators = [evidence_items[0].locator] if evidence_items else []
        claims.append(claim(
            "Grid binning is a coarse stand-in for kernel density estimation; a cell boundary can split one "
            "real concentration into two."
        ))

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=evidence_items,
            traces=[result.trace],
            payload=StructuredPayload(
                payload_type="map",
                title=f"Case concentrations, last {result.window_days} days",
                data={
                    "grid_metres": result.grid_metres,
                    "cells": [
                        {
                            "cell_id": cell.cell_id, "lat": cell.centroid_lat, "lon": cell.centroid_lon,
                            "case_count": cell.case_count, "intensity": cell.intensity,
                            "top_crime_sub_head": cell.top_crime_sub_head,
                            "case_master_ids": cell.case_ids[:50],
                        }
                        for cell in result.cells
                    ],
                },
            ),
            data={"case_master_ids": [cid for cell in result.cells for cid in cell.case_ids][:200]},
        )

    # -------------------------------------------------------- seasonality
    def _seasonal(self, request: AgentRequest) -> AgentResult:
        start, end = default_date_window(request.slots, today=request.today, months=SEASONAL_LOOKBACK_MONTHS)
        filters = AggregateFilter(
            unit_ids=request.slots.unit_ids or None,
            district_ids=request.slots.district_ids or None,
            crime_sub_head_ids=request.slots.crime_sub_head_ids or None,
            date_from=start,
            date_to=end,
        )
        result = self._engine.seasonality(filters, request.scope)
        scope_label = self._scope_label(request)

        if not result.buckets:
            nothing = empty_result_evidence(
                key=f"seasonality:none:{start}:{end}",
                label=f"No registered cases between {start} and {end} to compare by calendar month",
                detail={"window": [str(start), str(end)]},
            )
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(
                    "No registered cases fall inside that filter, so there is no calendar pattern to compare.",
                    [nothing], provenance=Provenance.DETERMINISTIC_COMPUTATION,
                )],
                evidence=[nothing], traces=[result.trace], confidence=0.85,
            )

        reportable = [b for b in result.buckets if not b.insufficient_history]
        if not reportable:
            nothing = empty_result_evidence(
                key=f"seasonality:insufficient:{start}:{end}",
                label=f"Fewer than 2 prior years of history for every calendar month {scope_label}",
                detail={"window": [str(start), str(end)], "months_seen": result.total_periods_considered},
            )
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(
                    f"The available history ({start} to {end}) does not yet cover enough separate years for any "
                    "calendar month to have a reliable seasonal baseline, so no seasonal finding is reported.",
                    [nothing], provenance=Provenance.DETERMINISTIC_COMPUTATION,
                )],
                evidence=[nothing], traces=[result.trace], confidence=0.85,
            )

        evidence_items = []
        claims = [claim(
            f"{len(reportable)} of {len(result.buckets)} calendar month(s) {scope_label} have enough history "
            "(at least 2 prior years) to compare against a seasonal baseline.",
            provenance=Provenance.DETERMINISTIC_COMPUTATION,
        )]
        ranked = sorted(reportable, key=lambda b: abs(b.z_score or 0.0), reverse=True)
        for bucket in ranked[:5]:
            item = aggregate_evidence(
                key=f"seasonality:{bucket.key}:{bucket.current_period}",
                label=f"{bucket.label} {bucket.current_period}: {bucket.current_count} case(s)",
                case_master_ids=bucket.case_ids,
                detail={"baseline_mean": bucket.baseline_mean, "baseline_years": bucket.baseline_years,
                        "z_score": bucket.z_score, "deviation_percent": bucket.deviation_percent},
            )
            evidence_items.append(item)
            if bucket.deviation_percent is None:
                change_text = f"against a baseline of {bucket.baseline_mean:.1f} in prior year(s)"
            else:
                direction = "above" if bucket.deviation_percent >= 0 else "below"
                change_text = f"{abs(bucket.deviation_percent):.1f}% {direction} its baseline of {bucket.baseline_mean:.1f}"
            claims.append(claim(
                f"{bucket.label} {bucket.current_period}: {bucket.current_count} case(s), {change_text} "
                f"(mean of {bucket.label} in {', '.join(map(str, bucket.baseline_years))}).",
                [item], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        if evidence_items:
            claims[0].evidence_locators = [evidence_items[0].locator]
        insufficient_months = [b.label for b in result.buckets if b.insufficient_history]
        if insufficient_months:
            claims.append(claim(
                f"{', '.join(insufficient_months)} do not yet have enough prior-year history and are excluded "
                "from this comparison."
            ))
        claims.append(claim(
            "These compare recorded FIR counts for a specific calendar month against that same month in prior "
            "years. This is a historical comparison, not a forecast of what will happen next time that month "
            "occurs."
        ))

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=evidence_items,
            traces=[result.trace],
            payload=StructuredPayload(
                payload_type="bar",
                title=f"Calendar-month pattern — {scope_label}",
                data={
                    "labels": [b.label for b in result.buckets],
                    "series": [{"name": "Most recent year", "values": [b.current_count for b in result.buckets]}],
                    "seasonal_detail": [
                        {
                            "month": b.label,
                            "current_period": b.current_period,
                            "current_count": b.current_count,
                            "baseline_mean": b.baseline_mean,
                            "deviation_percent": b.deviation_percent,
                            "z_score": b.z_score,
                            "insufficient_history": b.insufficient_history,
                        }
                        for b in result.buckets
                    ],
                },
            ),
            data={"case_master_ids": [cid for b in reportable for cid in b.case_ids][:200]},
        )

    # -------------------------------------------------------- early warning
    def _early_warning(self, request: AgentRequest) -> AgentResult:
        stored = self._alerts.alerts(district_ids=request.slots.district_ids or None, limit=10)
        if stored:
            alerts = stored
            source = "the most recent scheduled early-warning run"
        else:
            computed = self._engine.early_warning(
                request.scope, as_of=request.today, district_ids=request.slots.district_ids or None
            )
            alerts = [
                {
                    "alert_id": a.alert_id, "scope_name": a.scope_name, "crime_sub_head": a.crime_sub_head,
                    "observed_count": a.observed_count, "baseline_mean": a.baseline_mean,
                    "z_score": a.z_score, "severity": a.severity, "case_ids": a.case_ids,
                    "window_start": a.window_start, "window_end": a.window_end, "explanation": a.explanation,
                }
                for a in computed
            ]
            source = "an on-demand computation"

        if not alerts:
            nothing = empty_result_evidence(
                key="early_warning:none",
                label="No district × crime sub-head exceeded its own baseline",
                detail={"sigma_threshold": 2.0, "baseline_months": 12, "window_days": 30},
            )
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(
                    "No district and crime-type combination is currently running above its own 12-month baseline "
                    "by the configured threshold.",
                    [nothing], provenance=Provenance.DETERMINISTIC_COMPUTATION,
                )],
                evidence=[nothing],
                traces=[trace("early_warning",
                              "Compared each district × crime sub-head trailing-30-day count against its own "
                              "12-month monthly baseline using a z-score.",
                              inputs={"sigma_threshold": 2.0}, row_count=0)],
                confidence=0.85,
            )

        evidence_items = []
        claims = [claim(
            f"{len(alerts)} early-warning signal(s) are active, from {source}.",
            provenance=Provenance.DETERMINISTIC_COMPUTATION,
        )]
        for alert in alerts[:5]:
            item = alert_evidence(
                alert_id=str(alert["alert_id"]),
                label=f"{alert['scope_name']} · {alert.get('crime_sub_head') or 'all types'}",
                case_master_ids=[int(c) for c in alert.get("case_ids", [])],
                detail=alert.get("explanation", {}),
            )
            evidence_items.append(item)
            claims.append(claim(
                f"{alert['scope_name']}: {alert['observed_count']} {alert.get('crime_sub_head') or ''} case(s) "
                f"in the window ending {alert['window_end']}, against a monthly baseline of "
                f"{float(alert['baseline_mean']):.1f} — z = {float(alert['z_score']):.2f} ({alert['severity']}).",
                [item], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        claims[0].evidence_locators = [evidence_items[0].locator]
        claims.append(claim(
            "A z-score flags a statistical departure from a unit's own recent history. It is a prompt to look, "
            "not a conclusion that crime has risen."
        ))

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=evidence_items,
            traces=[trace(
                "early_warning",
                "For each district × crime sub-head, counted cases in the trailing window and compared them to "
                "the mean and standard deviation of that combination's own preceding monthly counts.",
                inputs={"alerts": len(alerts)},
                formula="z = (observed − mean(baseline)) / max(stddev(baseline), 1.0)",
                row_count=len(alerts),
            )],
            payload=StructuredPayload(
                payload_type="table",
                title="Active early-warning signals",
                data={
                    "columns": ["Area", "Crime type", "Observed", "Baseline", "z", "Severity"],
                    "rows": [
                        [str(a["scope_name"]), str(a.get("crime_sub_head") or "all"),
                         str(a["observed_count"]), f"{float(a['baseline_mean']):.1f}",
                         f"{float(a['z_score']):.2f}", str(a["severity"])]
                        for a in alerts
                    ],
                },
            ),
            data={"case_master_ids": [int(c) for a in alerts for c in a.get("case_ids", [])][:200],
                  "alert_ids": [a["alert_id"] for a in alerts]},
        )

    # ---------------------------------------------------------- sociology
    def _sociology(self, request: AgentRequest) -> AgentResult:
        subject = str(request.options.get("subject") or self._infer_subject(request.text_english))
        dimension = str(request.options.get("dimension") or self._infer_dimension(request.text_english))
        self._authorization.assert_aggregate_only_dimension(request.principal, dimension)

        substitution_note = None
        if subject == "victim" and dimension not in VICTIM_DIMENSIONS:
            substitution_note = (
                f"The victim record carries only age and gender, not {dimension.replace('_', ' ')} — showing "
                "gender instead."
            )
            dimension = "gender"

        start, end = default_date_window(request.slots, today=request.today, months=24)
        filters = AggregateFilter(
            unit_ids=request.slots.unit_ids or None,
            district_ids=request.slots.district_ids or None,
            crime_sub_head_ids=request.slots.crime_sub_head_ids or None,
            date_from=start, date_to=end,
        )
        result = self._engine.sociology(filters, request.scope, dimension=dimension, subject=subject)
        if result.total_records == 0:
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(f"No {subject} records fall inside that filter.")],
                traces=[result.trace], confidence=0.85,
            )

        evidence = aggregate_evidence(
            key=f"sociology:{subject}:{dimension}:{start}:{end}",
            label=f"{result.total_records} {subject} records grouped by {dimension}",
            case_master_ids=result.case_ids,
            detail={"dimension": dimension, "subject": subject, "window": [str(start), str(end)],
                    "suppression_threshold": result.suppression_threshold},
        )
        claims = []
        if substitution_note:
            claims.append(claim(substitution_note))
        claims.append(claim(
            f"{result.total_records} {subject} record(s) between {start} and {end} break down by "
            f"{dimension.replace('_', ' ')} as follows.",
            [evidence], provenance=Provenance.DETERMINISTIC_COMPUTATION,
        ))
        shown = list(result.top_associations[:5])
        suppressed_entry = next((row for row in result.top_associations if row.get("suppressed")), None)
        if suppressed_entry and suppressed_entry not in shown:
            shown.append(suppressed_entry)
        for row in shown:
            if row.get("suppressed"):
                claims.append(claim(
                    f"{row['value']}: {row['records']} record(s) combined, {row['share_percent']:.1f}% of the "
                    "total — shown as a merged group rather than individually so a small group cannot be "
                    "re-identified.",
                    [evidence], provenance=Provenance.DETERMINISTIC_COMPUTATION,
                ))
                continue
            top_type = row["top_crime_sub_heads"][0]["sub_head"] if row["top_crime_sub_heads"] else "unclassified"
            claims.append(claim(
                f"{row['value']}: {row['records']} record(s), {row['share_percent']:.1f}% of the total; "
                f"most common associated offence type is {top_type}.",
                [evidence], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        claims.append(claim(
            f"These are counts of recorded {subject}s, not rates against population. Differences in reporting "
            "propensity, policing intensity and base rates all affect them, so association here is not causation "
            "and must not be read as a statement about any community."
        ))

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=[evidence],
            traces=[result.trace],
            payload=StructuredPayload(
                payload_type="bar",
                title=f"{subject.capitalize()} records by {dimension.replace('_', ' ')}",
                data={
                    "labels": [row["value"] for row in result.top_associations],
                    "series": [{"name": "Records", "values": [row["records"] for row in result.top_associations]}],
                },
            ),
            data={"dimension": dimension, "subject": subject, "case_master_ids": result.case_ids[:200]},
        )

    @staticmethod
    def _infer_subject(text: str) -> str:
        lowered = text.casefold()
        if "victim" in lowered:
            return "victim"
        return "complainant"

    @staticmethod
    def _infer_dimension(text: str) -> str:
        lowered = text.casefold()
        for dimension in SOCIOLOGY_DIMENSIONS:
            if dimension.replace("_", " ") in lowered:
                return dimension
        if "age" in lowered:
            return "age_band"
        if "job" in lowered or "work" in lowered or "profession" in lowered:
            return "occupation"
        if "gender" in lowered or "women" in lowered or "men" in lowered:
            return "gender"
        return "occupation"

    def _scope_label(self, request: AgentRequest) -> str:
        if request.slots.district_names:
            return " and ".join(request.slots.district_names[:3])
        if request.slots.unit_names:
            return " and ".join(request.slots.unit_names[:3])
        return "across your authorized area"
