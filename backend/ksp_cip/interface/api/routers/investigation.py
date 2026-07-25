"""Investigation support: briefings, timelines and the priority indicator."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from ....application.agents import AgentRequest
from ....domain.enums import Intent, Permission
from ....domain.errors import NotFoundError
from ....domain.models import Principal
from ..deps import ContainerDep, PrincipalDep, require, scope_note

router = APIRouter(prefix="/investigation", tags=["investigation"])


@router.get("/briefing/{crime_no}")
def briefing(
    crime_no: str,
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.READ_CASE_DETAIL)),
) -> dict[str, Any]:
    summary = container.cases.by_crime_no(crime_no, principal.scope)
    if summary is None:
        raise NotFoundError("No FIR with that CrimeNo is available within your authorized scope.",
                            crime_no=crime_no)
    from ....domain.models import Slots

    request = AgentRequest(
        principal=principal,
        intent=Intent.INVESTIGATION_SUMMARY,
        slots=Slots(case_master_ids=[summary.case_master_id]),
        scope=principal.scope,
        text_english=f"Brief me on FIR {crime_no}",
        session_id=f"api:{principal.user_id}",
        today=container.clock.now().date(),
    )
    result = container.investigation_support.handle(request)
    return {
        "crime_no": crime_no,
        "claims": [
            {"text": c.text, "evidence_locators": c.evidence_locators, "provenance": str(c.provenance)}
            for c in result.summary_claims
        ],
        "evidence": [item.model_dump(mode="json") for item in result.evidence],
        "traces": [t.model_dump(mode="json") for t in result.traces],
        "payload": result.payload.model_dump(mode="json"),
        "scope_note": scope_note(principal),
        "notice": (
            "This briefing restates what is recorded in the FIR system. It is an intelligence "
            "product for investigative use, not evidence, and does not replace the case diary."
        ),
    }


@router.get("/priority")
def priority(
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.READ_CASE_DETAIL)),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    from ....infrastructure.db.repositories import CaseFilter

    candidates = container.cases.search(CaseFilter(limit=500), principal.scope)
    ranked = container.priority.top([c.case_master_id for c in candidates], limit=limit)
    by_id = {c.case_master_id: c for c in candidates}
    return {
        "cases": [
            {
                "case": by_id[int(row["case_master_id"])].model_dump(mode="json"),
                "score": row["score"],
                "band": row["band"],
                "components": row["components"],
            }
            for row in ranked if int(row["case_master_id"]) in by_id
        ],
        "scope_note": scope_note(principal),
        "formula": "Sum of published component weights, capped at 100.",
        "notice": (
            "The indicator orders attention across cases. It does not judge the merits of any case "
            "and carries no view about any individual."
        ),
    }
