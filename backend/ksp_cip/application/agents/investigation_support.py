"""InvestigationSupportAgent — case briefing, timeline, leads, priority.

This is the agent closest to an operational decision, so it is the most
conservative. It assembles a briefing entirely from records the caller is
already authorized to see, orders those records into a timeline, and offers
*investigative questions* rather than directions.

The Investigation Priority Indicator is a transparent weighted sum computed in
:class:`AnalyticsEngine`; every component and its weight is returned with the
score so a supervisor can disagree with the arithmetic rather than with a
black box.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ...domain.enums import AgentName, Intent, Permission, Provenance
from ...domain.models import AgentResult, CaseSummary, StructuredPayload
from ...infrastructure.db.repositories import CaseRepository, IdentityRepository, PriorityRepository
from ..analytics import AnalyticsEngine
from ..graph import GraphService, person_node
from ..rag import RetrievalService
from ..services.audit import AuditService, audited
from ..services.authorization import AuthorizationService
from ..services.evidence import aggregate_evidence, case_evidence, claim, person_evidence, trace
from .base import AgentRequest, BaseAgent


class InvestigationSupportAgent(BaseAgent):
    name = AgentName.INVESTIGATION_SUPPORT

    def __init__(
        self,
        audit: AuditService,
        cases: CaseRepository,
        engine: AnalyticsEngine,
        priority: PriorityRepository,
        retrieval: RetrievalService,
        graph: GraphService,
        identities: IdentityRepository,
        authorization: AuthorizationService,
    ) -> None:
        super().__init__(audit)
        self._cases = cases
        self._engine = engine
        self._priority = priority
        self._retrieval = retrieval
        self._graph = graph
        self._identities = identities
        self._authorization = authorization

    @audited("agent.investigation_support", object_type="case")
    def handle(self, request: AgentRequest) -> AgentResult:
        request.principal.require(Permission.READ_CASE_DETAIL)
        summary = self._resolve_case(request)
        if summary is None:
            return self._priority_queue(request)
        return self._briefing(request, summary)

    def _resolve_case(self, request: AgentRequest) -> CaseSummary | None:
        if request.slots.crime_nos:
            found = self._cases.by_crime_no(request.slots.crime_nos[0], request.scope)
            if found:
                return found
        for case_id in (*request.slots.case_master_ids, *request.pinned_case_master_ids):
            found = self._cases.by_id(case_id, request.scope)
            if found:
                return found
        return None

    # ------------------------------------------------------------ briefing
    def _briefing(self, request: AgentRequest, summary: CaseSummary) -> AgentResult:
        case_id = summary.case_master_id
        case_ids = [case_id]
        accused = self._cases.accused_for_cases(case_ids)
        victims = self._cases.victims_for_cases(case_ids)
        complainants = self._cases.complainants_for_cases(
            case_ids, include_sensitive=request.principal.has(Permission.READ_SENSITIVE_DEMOGRAPHICS)
        )
        act_sections = self._cases.act_sections_for_cases(case_ids)
        arrests = self._cases.arrests_for_cases(case_ids)
        chargesheets = self._cases.chargesheets_for_cases(case_ids)
        officer = self._cases.officer_for_case(case_id)

        base = case_evidence(summary)
        evidence_items = [base]
        claims = [
            claim(
                f"FIR {summary.crime_no} was registered at "
                f"{summary.police_station_name or 'an unrecorded station'} on "
                f"{summary.crime_registered_date or 'a date not recorded'} and is classified as "
                f"{summary.crime_sub_head or 'unclassified'} under {summary.crime_head or 'no major head'}.",
                [base],
            ),
            claim(
                f"Its current status is {summary.status or 'not recorded'}"
                + (f", before {summary.court_name}" if summary.court_name else "")
                + ".",
                [base],
            ),
        ]
        if act_sections:
            rendered = ", ".join(
                f"{row.get('ShortName') or row.get('ActID')} §{row.get('SectionID')}"
                for row in act_sections[:6]
            )
            claims.append(claim(f"Charges are framed under {rendered}.", [base]))
        if summary.brief_facts:
            claims.append(claim(f"Recorded facts: {summary.brief_facts.strip()[:600]}", [base]))
        if complainants:
            person = complainants[0]
            claims.append(claim(
                f"The complaint was made by {person.name}"
                + (f", age {person.age_year}" if person.age_year else "")
                + ".",
                [base],
            ))
        if victims:
            claims.append(claim(
                f"{len(victims)} victim(s) are recorded: "
                + ", ".join(f"{v.name}{f' ({v.age_year})' if v.age_year else ''}" for v in victims[:4])
                + ".",
                [base],
            ))
        if accused:
            claims.append(claim(
                f"{len(accused)} accused are named: "
                + ", ".join(f"{a.person_ref or ''} {a.name}".strip() for a in accused[:6])
                + ".",
                [base],
            ))
        else:
            claims.append(claim("No accused is named on this FIR.", [base]))
        if arrests:
            claims.append(claim(
                f"{len(arrests)} arrest or surrender event(s) are recorded, the most recent on "
                f"{max(str(a.get('ArrestSurrenderDate') or '') for a in arrests)[:10]}.",
                [base], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        else:
            claims.append(claim("No arrest or surrender is recorded against this FIR.", [base]))
        if chargesheets:
            latest = max(chargesheets, key=lambda c: str(c.get("csdate") or ""))
            claims.append(claim(
                f"A final report of type {latest.get('cstype')} was filed on {str(latest.get('csdate'))[:10]}.",
                [base],
            ))
        if officer:
            claims.append(claim(
                f"The registering officer is {officer.get('FirstName')} "
                f"({officer.get('RankName') or 'rank not recorded'}).",
                [base],
            ))

        timeline = self._timeline(summary, arrests, chargesheets)
        priority = self._priority_for(request, summary, accused_count=len(accused))
        if priority:
            priority_evidence = aggregate_evidence(
                key=f"ipi:{case_id}",
                label=f"Investigation priority indicator for FIR {summary.crime_no}",
                case_master_ids=[case_id],
                crime_nos=[summary.crime_no],
                detail={"components": priority["components"]},
            )
            evidence_items.append(priority_evidence)
            claims.append(claim(
                f"Investigation priority indicator: {priority['score']:.0f}/100 ({priority['band']}), "
                f"driven by {priority['top_driver']}.",
                [priority_evidence], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))

        leads, lead_evidence = self._leads(request, summary, accused)
        evidence_items.extend(lead_evidence)
        claims.extend(leads)
        claims.append(claim(
            "This briefing restates what is recorded in the FIR system. It is an intelligence product for "
            "investigative use, not evidence, and it does not replace the case diary."
        ))

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=evidence_items,
            traces=[trace(
                "case_briefing",
                f"Assembled FIR {summary.crime_no} with its complainant, victim, accused, act-section, "
                "arrest and chargesheet child records, ordered every dated event into a timeline, and computed "
                "the priority indicator from published weights.",
                inputs={"case_master_id": case_id, "accused": len(accused), "victims": len(victims),
                        "arrests": len(arrests), "chargesheets": len(chargesheets)},
                row_count=1 + len(accused) + len(victims) + len(arrests) + len(chargesheets),
                components=priority["components"]["items"] if priority else [],
            )],
            payload=StructuredPayload(
                payload_type="timeline",
                title=f"FIR {summary.crime_no} — case timeline",
                data={
                    "crime_no": summary.crime_no,
                    "events": timeline,
                    "priority": priority,
                    "counts": {
                        "accused": len(accused), "victims": len(victims),
                        "arrests": len(arrests), "chargesheets": len(chargesheets),
                    },
                },
            ),
            data={"case_master_ids": [case_id], "crime_no": summary.crime_no,
                  "person_names": [a.name for a in accused][:5]},
        )

    @staticmethod
    def _timeline(
        summary: CaseSummary, arrests: list[dict[str, Any]], chargesheets: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []

        def add(when: Any, label: str, detail: str, kind: str) -> None:
            if not when:
                return
            events.append({"at": str(when)[:19], "label": label, "detail": detail, "kind": kind})

        add(summary.incident_from_date, "Incident began", "IncidentFromDate on the FIR", "incident")
        add(summary.incident_to_date, "Incident ended", "IncidentToDate on the FIR", "incident")
        add(summary.info_received_ps_date, "Information received at station",
            "InfoReceivedPSDate on the FIR", "report")
        add(summary.crime_registered_date, "FIR registered",
            f"CrimeNo {summary.crime_no} at {summary.police_station_name or 'station not recorded'}", "registration")
        for arrest in arrests:
            add(arrest.get("ArrestSurrenderDate"),
                "Arrest or surrender",
                f"Recorded against accused id {arrest.get('AccusedMasterID')}", "arrest")
        for sheet in chargesheets:
            add(sheet.get("csdate"), "Final report filed",
                f"Type {sheet.get('cstype')}", "chargesheet")
        events.sort(key=lambda item: item["at"])
        return events

    def _priority_for(
        self, request: AgentRequest, summary: CaseSummary, *, accused_count: int
    ) -> dict[str, Any] | None:
        stored = self._priority.for_case(summary.case_master_id)
        if stored:
            components = stored.get("components", {})
            items = components.get("items", [])
            top = max(items, key=lambda i: i.get("weight", 0), default=None)
            return {
                "score": float(stored["score"]),
                "band": stored["band"],
                "components": components,
                "top_driver": top["name"] if top else "recorded case attributes",
            }
        computed = self._engine.priority_for_summary(
            summary, accused_count=accused_count, as_of=request.today
        )
        if computed is None:
            return None
        items = computed["components"]["items"]
        top = max(items, key=lambda i: i.get("weight", 0), default=None)
        return {
            "score": computed["score"], "band": computed["band"],
            "components": computed["components"],
            "top_driver": top["name"] if top else "recorded case attributes",
        }

    def _leads(self, request: AgentRequest, summary: CaseSummary, accused: list[Any]) -> tuple[list[Any], list[Any]]:
        claims: list[Any] = []
        evidence_items: list[Any] = []

        similar = self._retrieval.similar_to_case(summary.case_master_id, request.scope, top_k=3)
        if similar:
            related = self._cases.by_ids([d.case_master_id for d in similar], request.scope)
            for document, case in zip(similar, related):
                item = case_evidence(case)
                evidence_items.append(item)
                claims.append(claim(
                    f"Comparable record to review: FIR {case.crime_no} "
                    f"({case.crime_sub_head or 'unclassified'}, {case.district_name or 'district not recorded'}), "
                    f"text similarity {document.similarity:.2f}.",
                    [item], provenance=Provenance.DETERMINISTIC_COMPUTATION,
                ))

        for person in accused[:2]:
            identity = self._identities.identity_for_accused(person.record_id)
            if not identity:
                continue
            other_cases = [int(c) for c in identity.get("case_ids", []) if int(c) != summary.case_master_id]
            if not other_cases:
                continue
            other_crime_nos = [
                found.crime_no
                for found in self._cases.by_ids(other_cases, request.scope)
            ]
            item = person_evidence(
                identity_id=str(identity["identity_id"]),
                label=str(identity["canonical_name"]),
                case_master_ids=other_cases,
                crime_nos=other_crime_nos,
            )
            evidence_items.append(item)
            claims.append(claim(
                f"{person.name} is linked by name and profile matching to {len(other_cases)} other FIR(s); "
                "those records are worth pulling.",
                [item], provenance=Provenance.INFERRED,
            ))
            associates = self._graph.neighbours_of_type(
                person_node(str(identity["identity_id"])), "person:", request.scope
            )
            if associates:
                claims.append(claim(
                    f"{person.name} shares FIR records with {len(associates)} other person(s) in the link graph.",
                    [item], provenance=Provenance.INFERRED,
                ))

        if claims:
            claims.append(claim(
                "These are pointers to existing records, not investigative conclusions. Verify each against "
                "the source FIR before acting."
            ))
        return claims, evidence_items

    # ------------------------------------------------------ priority queue
    def _priority_queue(self, request: AgentRequest) -> AgentResult:
        from ...infrastructure.db.repositories import CaseFilter

        filters = CaseFilter(
            unit_ids=request.slots.unit_ids or None,
            district_ids=request.slots.district_ids or None,
            crime_sub_head_ids=request.slots.crime_sub_head_ids or None,
            date_from=request.slots.date_from,
            date_to=request.slots.date_to,
            limit=200,
        )
        candidates = self._cases.search(filters, request.scope)
        if not candidates:
            return self.empty_result(
                request,
                "No FIR is available in your authorized scope for that filter.",
                clarification="Which FIR should I brief? A CrimeNo, or a district and crime type, is enough.",
            )
        scored = self._priority.top([c.case_master_id for c in candidates], limit=10)
        if not scored:
            computed = []
            for case in candidates[:60]:
                result = self._engine.priority_for_summary(case, accused_count=0, as_of=request.today)
                if result:
                    computed.append({**result, "case_master_id": case.case_master_id,
                                     "crime_no": case.crime_no})
            scored = sorted(computed, key=lambda item: item["score"], reverse=True)[:10]
        if not scored:
            return self.empty_result(request, "No case in that filter produced a priority score.")

        by_id = {c.case_master_id: c for c in candidates}
        evidence_items = []
        claims = [claim(
            f"{len(scored)} case(s) rank highest on the investigation priority indicator within that filter.",
            provenance=Provenance.DETERMINISTIC_COMPUTATION,
        )]
        for row in scored:
            case = by_id.get(int(row["case_master_id"]))
            if case is None:
                continue
            item = case_evidence(case)
            evidence_items.append(item)
            claims.append(claim(
                f"FIR {case.crime_no} — {float(row['score']):.0f}/100 ({row['band']}), "
                f"{case.crime_sub_head or 'unclassified'}, status {case.status or 'not recorded'}.",
                [item], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        claims[0].evidence_locators = [evidence_items[0].locator] if evidence_items else []
        claims.append(claim(
            "The indicator weighs offence gravity, time pending, investigative progress, network reach and "
            "linked alerts. It orders attention; it does not judge the merits of any case."
        ))

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=evidence_items,
            traces=[trace(
                "investigation_priority",
                "Scored each candidate FIR with the published weighted-sum indicator and ranked the results.",
                inputs={"candidates": len(candidates), "returned": len(scored)},
                formula="IPI = gravity + days pending + progress + network reach + offender history + alert link",
                row_count=len(scored),
            )],
            payload=StructuredPayload(
                payload_type="table", title="Cases by investigation priority",
                data={
                    "columns": ["CrimeNo", "Crime type", "Status", "Score", "Band"],
                    "rows": [
                        [by_id[int(r["case_master_id"])].crime_no,
                         by_id[int(r["case_master_id"])].crime_sub_head or "",
                         by_id[int(r["case_master_id"])].status or "",
                         f"{float(r['score']):.0f}", str(r["band"])]
                        for r in scored if int(r["case_master_id"]) in by_id
                    ],
                },
            ),
            data={"case_master_ids": [int(r["case_master_id"]) for r in scored]},
        )
