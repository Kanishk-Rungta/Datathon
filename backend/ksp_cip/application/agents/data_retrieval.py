"""DataRetrievalAgent — parameterized reads and semantic retrieval.

Covers the plan's Retrieval Agent and RAG Agent: structured lookups over the
curated schema, and vector retrieval for "cases like this". It is the only
agent that reads case rows directly, which keeps the authorization surface
small and makes the audit trail legible.

Hard rule enforced here: a result row leaves this agent only with its
``CaseMasterID`` and ``CrimeNo`` attached.
"""

from __future__ import annotations

from typing import Any

from ...domain.enums import AgentName, Intent, Permission, Provenance
from ...domain.models import AgentResult, CaseSummary, StructuredPayload
from ...infrastructure.db.repositories import CaseFilter, CaseRepository, ReferenceRepository
from ..nlu import default_date_window
from ..rag import RetrievalService
from ..services.audit import AuditService, audited
from ..services.authorization import AuthorizationService
from ..services.evidence import aggregate_evidence, case_evidence, claim, trace
from .base import AgentRequest, BaseAgent


class DataRetrievalAgent(BaseAgent):
    name = AgentName.DATA_RETRIEVAL

    def __init__(
        self,
        audit: AuditService,
        cases: CaseRepository,
        reference: ReferenceRepository,
        retrieval: RetrievalService,
        authorization: AuthorizationService,
        *,
        default_page_size: int = 25,
    ) -> None:
        super().__init__(audit)
        self._cases = cases
        self._reference = reference
        self._retrieval = retrieval
        self._authorization = authorization
        self._page_size = default_page_size

    @audited("agent.data_retrieval", object_type="case")
    def handle(self, request: AgentRequest) -> AgentResult:
        request.principal.require(Permission.READ_CASE_DETAIL)
        if request.intent is Intent.SIMILAR_CASE:
            return self._similar_cases(request)
        if request.intent is Intent.LOOKUP_PERSON:
            return self._person_history(request)
        return self._case_search(request)

    # ------------------------------------------------------------- lookups
    def _case_search(self, request: AgentRequest) -> AgentResult:
        slots = request.slots

        # If the officer named a place the master data does not contain, say so.
        # Dropping the term and answering statewide would be answering a
        # different, broader question than the one asked.
        if slots.unresolved_terms and not (slots.district_ids or slots.unit_ids):
            named = ", ".join(f"'{term}'" for term in slots.unresolved_terms[:3])
            return AgentResult(
                agent=self.name,
                intent=request.intent,
                summary_claims=[claim(
                    f"I could not match {named} to any district or police station in the records, "
                    "so I have not run a search. Naming a Karnataka district or station will work."
                )],
                traces=[trace(
                    "location_resolution",
                    "Compared the named place against curated_District and curated_Unit, including a "
                    "fuzzy match for spelling variants, and found nothing.",
                    inputs={"unresolved": slots.unresolved_terms[:5]}, row_count=0,
                )],
                confidence=0.9,
                needs_clarification="Which district or police station did you mean?",
            )

        status_ids = [
            int(row["CaseStatusID"])
            for row in self._reference.case_statuses()
            if row["CaseStatusName"] in slots.status_names
        ]
        filters = CaseFilter(
            unit_ids=slots.unit_ids or None,
            district_ids=slots.district_ids or None,
            crime_sub_head_ids=slots.crime_sub_head_ids or None,
            status_ids=status_ids or None,
            case_master_ids=slots.case_master_ids or None,
            crime_nos=slots.crime_nos or None,
            date_from=slots.date_from,
            date_to=slots.date_to,
            limit=slots.limit or self._page_size,
        )
        results = self._cases.search(filters, request.scope)
        total = self._cases.count(filters, request.scope)

        if not results:
            return AgentResult(
                agent=self.name,
                intent=request.intent,
                summary_claims=[claim(
                    "No FIR in the indexed records matches that description within your authorized scope."
                )],
                traces=[trace("case_search", self._describe_filter(filters), inputs=_filter_summary(filters),
                              row_count=0)],
                confidence=0.9,
            )

        evidence = [case_evidence(case) for case in results]
        aggregate = aggregate_evidence(
            key=f"case_search:{request.session_id}",
            label=f"{total} matching FIR(s) in curated_CaseMaster",
            case_master_ids=[case.case_master_id for case in results],
            crime_nos=[case.crime_no for case in results],
            detail=_filter_summary(filters),
        )
        claims = [
            claim(
                f"{total} FIR{'s' if total != 1 else ''} match{'' if total != 1 else 'es'} that description; "
                f"the {len(results)} most recent are listed below.",
                [aggregate],
                provenance=Provenance.DETERMINISTIC_COMPUTATION,
            )
        ]
        for case, item in list(zip(results, evidence))[:5]:
            claims.append(claim(self._case_line(case), [item]))

        return AgentResult(
            agent=self.name,
            intent=request.intent,
            summary_claims=claims,
            evidence=[aggregate, *evidence],
            traces=[trace("case_search", self._describe_filter(filters), inputs=_filter_summary(filters),
                          row_count=total)],
            payload=StructuredPayload(
                payload_type="table",
                title="Matching FIRs",
                data={
                    "columns": ["CrimeNo", "Registered", "Police station", "Crime type", "Status"],
                    "rows": [
                        [case.crime_no,
                         case.crime_registered_date.isoformat() if case.crime_registered_date else "",
                         case.police_station_name or "", case.crime_sub_head or "", case.status or ""]
                        for case in results
                    ],
                    "cases": [case.model_dump(mode="json") for case in results],
                    "total": total,
                    "returned": len(results),
                },
            ),
            data={"cases": [case.model_dump(mode="json") for case in results],
                  "case_master_ids": [case.case_master_id for case in results],
                  "total": total},
        )

    def _person_history(self, request: AgentRequest) -> AgentResult:
        names = request.slots.person_names or request.pinned_person_names
        if not names:
            return self.empty_result(
                request,
                "I need a name to search for.",
                clarification="Which person should I look up? A full or partial name is enough.",
            )
        name = names[0]
        rows = self._cases.find_accused_by_name(name, request.scope, limit=120)
        if not rows:
            return AgentResult(
                agent=self.name,
                intent=request.intent,
                summary_claims=[claim(
                    f"No accused record matching '{name}' appears in the FIRs within your authorized scope."
                )],
                traces=[trace("person_lookup",
                              "Matched curated_Accused.AccusedName with a bound LIKE parameter, "
                              "restricted to the caller's unit subtree.",
                              inputs={"name": name}, row_count=0)],
                confidence=0.85,
            )

        case_ids = sorted({int(row["CaseMasterID"]) for row in rows})
        summaries = {c.case_master_id: c for c in self._cases.by_ids(case_ids, request.scope)}
        evidence = [case_evidence(summary) for summary in summaries.values()]
        aggregate = aggregate_evidence(
            key=f"person:{name}",
            label=f"{len(case_ids)} FIR(s) naming an accused matching '{name}'",
            case_master_ids=case_ids,
            crime_nos=[s.crime_no for s in summaries.values()],
            detail={"name_query": name, "match_mode": "substring on AccusedName"},
        )
        districts = sorted({str(row.get("DistrictName")) for row in rows if row.get("DistrictName")})
        crime_types = sorted({str(row.get("crime_sub_head")) for row in rows if row.get("crime_sub_head")})

        claims = [
            claim(
                f"'{name}' appears as an accused in {len(case_ids)} FIR(s) across "
                f"{len(districts)} district(s).",
                [aggregate],
                provenance=Provenance.DETERMINISTIC_COMPUTATION,
            )
        ]
        if crime_types:
            claims.append(claim(
                "Recorded offence types: " + ", ".join(crime_types[:6]) + ".",
                [aggregate],
                provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        for summary in list(summaries.values())[:5]:
            claims.append(claim(self._case_line(summary), [case_evidence(summary)]))
        claims.append(claim(
            "Name matching is textual only. Two people can share a name, and one person can appear "
            "under spelling variants; treat this as a starting point, not an identity."
        ))

        return AgentResult(
            agent=self.name,
            intent=request.intent,
            summary_claims=claims,
            evidence=[aggregate, *evidence],
            traces=[trace(
                "person_lookup",
                f"Selected curated_Accused rows whose AccusedName contains '{name}', joined to "
                "curated_CaseMaster, restricted to the caller's authorized units.",
                inputs={"name": name}, row_count=len(rows),
            )],
            payload=StructuredPayload(
                payload_type="table",
                title=f"FIRs naming '{name}'",
                data={
                    "columns": ["CrimeNo", "Registered", "District", "Crime type", "Age recorded"],
                    "rows": [
                        [str(row["CrimeNo"]), str(row.get("CrimeRegisteredDate") or "")[:10],
                         str(row.get("DistrictName") or ""), str(row.get("crime_sub_head") or ""),
                         str(row.get("AgeYear") or "")]
                        for row in rows[:25]
                    ],
                },
            ),
            data={"case_master_ids": case_ids, "person_name": name,
                  "accused_master_ids": [int(row["AccusedMasterID"]) for row in rows]},
        )

    def _similar_cases(self, request: AgentRequest) -> AgentResult:
        anchor_ids = request.slots.case_master_ids or request.pinned_case_master_ids
        anchor: CaseSummary | None = None
        if request.slots.crime_nos:
            anchor = self._cases.by_crime_no(request.slots.crime_nos[0], request.scope)
        elif anchor_ids:
            anchor = self._cases.by_id(anchor_ids[0], request.scope)

        if anchor is not None:
            documents = self._retrieval.similar_to_case(anchor.case_master_id, request.scope)
            query_description = f"BriefFacts and classification of FIR {anchor.crime_no}"
        else:
            documents = self._retrieval.search(
                request.text_english, request.scope,
                boost_terms=[*request.slots.crime_types, *request.slots.district_names],
            )
            query_description = "the free text of your question"

        if not documents:
            return AgentResult(
                agent=self.name,
                intent=request.intent,
                summary_claims=[claim(
                    "No confident match. Nothing in the indexed case text is close enough to report, so "
                    "I am returning nothing rather than a weak guess."
                )],
                traces=[trace("semantic_retrieval",
                              f"Embedded {query_description} with {self._retrieval.model_name} and compared it by "
                              "cosine similarity against the case corpus, filtered to your authorized units first.",
                              inputs={"top_k": 0}, row_count=self._retrieval.document_count)],
                confidence=0.8,
            )

        summaries = {c.case_master_id: c for c in
                     self._cases.by_ids([d.case_master_id for d in documents], request.scope)}
        evidence = [case_evidence(summaries[d.case_master_id]) for d in documents if d.case_master_id in summaries]
        claims = [claim(
            f"{len(documents)} case(s) in the indexed records resemble {query_description}.",
            evidence[:1] if evidence else [],
            provenance=Provenance.DETERMINISTIC_COMPUTATION,
        )]
        for document in documents[:5]:
            summary = summaries.get(document.case_master_id)
            if summary is None:
                continue
            claims.append(claim(
                f"{self._case_line(summary)} — similarity {document.similarity:.2f}.",
                [case_evidence(summary)],
                provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))

        return AgentResult(
            agent=self.name,
            intent=request.intent,
            summary_claims=claims,
            evidence=evidence,
            traces=[trace(
                "semantic_retrieval",
                f"Embedded {query_description} with {self._retrieval.model_name}, pre-filtered the "
                f"{self._retrieval.document_count}-document corpus to your authorized units, then ranked by "
                "cosine similarity with a keyword boost for matching crime type and district.",
                inputs={"model": self._retrieval.model_name, "returned": len(documents)},
                row_count=self._retrieval.document_count,
                formula="score = cosine(query, document) + 0.06 × matched boost terms",
            )],
            payload=StructuredPayload(
                payload_type="table",
                title="Similar cases",
                data={
                    "columns": ["CrimeNo", "Crime type", "District", "Similarity"],
                    "rows": [
                        [summaries[d.case_master_id].crime_no,
                         summaries[d.case_master_id].crime_sub_head or "",
                         summaries[d.case_master_id].district_name or "",
                         f"{d.similarity:.2f}"]
                        for d in documents if d.case_master_id in summaries
                    ],
                },
            ),
            data={"case_master_ids": [d.case_master_id for d in documents],
                  "anchor_case_master_id": anchor.case_master_id if anchor else None},
        )

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _case_line(case: CaseSummary) -> str:
        registered = case.crime_registered_date.isoformat() if case.crime_registered_date else "date not recorded"
        return (
            f"FIR {case.crime_no} — {case.crime_sub_head or 'unclassified'} at "
            f"{case.police_station_name or 'an unrecorded station'}, registered {registered}, "
            f"status {case.status or 'not recorded'}"
        )

    @staticmethod
    def _describe_filter(filters: CaseFilter) -> str:
        parts = ["Selected rows from curated_CaseMaster joined to unit, district, crime-head and status masters"]
        if filters.district_ids:
            parts.append(f"restricted to district id(s) {list(filters.district_ids)}")
        if filters.unit_ids:
            parts.append(f"restricted to police station id(s) {list(filters.unit_ids)}")
        if filters.crime_sub_head_ids:
            parts.append(f"restricted to crime sub-head id(s) {list(filters.crime_sub_head_ids)}")
        if filters.date_from or filters.date_to:
            parts.append(
                f"with CrimeRegisteredDate between {filters.date_from or 'the earliest record'} and "
                f"{filters.date_to or 'today'}"
            )
        parts.append("and always intersected with your authorized unit subtree")
        return ", ".join(parts) + "."


def _filter_summary(filters: CaseFilter) -> dict[str, Any]:
    return {
        "district_ids": list(filters.district_ids or []),
        "unit_ids": list(filters.unit_ids or []),
        "crime_sub_head_ids": list(filters.crime_sub_head_ids or []),
        "status_ids": list(filters.status_ids or []),
        "date_from": filters.date_from.isoformat() if filters.date_from else None,
        "date_to": filters.date_to.isoformat() if filters.date_to else None,
        "limit": filters.limit,
    }
