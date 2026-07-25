"""PDF export.

Exports are watermarked with the requesting officer and carry an explicit
"intelligence product, not evidence" header, because a PDF outlives the
conversation that produced it and will be read by people who never saw the
caveats on screen.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ....domain.enums import Permission
from ....domain.errors import NotFoundError, ValidationError
from ....domain.models import Principal
from ..deps import ContainerDep, PrincipalDep, require, scope_note
from ..schemas import ExportRequest, ExportResponse

router = APIRouter(prefix="/export", tags=["export"])

NOTICE = (
    "This document is an intelligence product generated from indexed police records. It is not "
    "evidence, it does not replace the case diary, and every figure in it should be verified "
    "against the cited FIR before use."
)


@router.post("/pdf", response_model=ExportResponse)
def export_pdf(
    payload: ExportRequest,
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.EXPORT_PDF)),
) -> ExportResponse:
    if payload.session_id:
        turns = [t for t in container.memory.transcript(payload.session_id)
                 if t["user_id"] == principal.user_id]
        if not turns:
            raise NotFoundError("That session has no turns to export.", session_id=payload.session_id)
        result = container.pdf.export_conversation(
            principal=principal,
            session_id=payload.session_id,
            turns=turns,
            scope_label=scope_note(principal),
        )
        filename = f"cip-conversation-{payload.session_id}.pdf"
    elif payload.case_master_id:
        summary = container.cases.by_id(payload.case_master_id, principal.scope)
        if summary is None:
            raise NotFoundError("That FIR is not available within your authorized scope.",
                                case_master_id=payload.case_master_id)
        result = container.pdf.export_case_briefing(
            principal=principal,
            case_reference=summary.crime_no,
            sections=_briefing_sections(container, principal, summary),
            scope_label=scope_note(principal),
        )
        filename = f"cip-briefing-{summary.crime_no}.pdf"
    else:
        raise ValidationError("Provide either session_id or case_master_id.")

    container.audit.record(
        action="export.pdf", principal=principal, object_type="export",
        object_ids=[result["key"]], outcome="success",
        detail={"bytes": result.get("bytes"), "session_id": payload.session_id,
                "case_master_id": payload.case_master_id},
    )
    return ExportResponse(
        url=container.filestore.url_for(result["key"]),
        key=result["key"],
        filename=filename,
        bytes=int(result.get("bytes", 0)),
        kannada_glyphs_embedded=container.pdf.supports_kannada_glyphs,
        notice=NOTICE,
    )


def _briefing_sections(container: Any, principal: Any, summary: Any) -> list[tuple[str, list[str]]]:
    """Flatten a case into the (heading, lines) shape the PDF service renders."""
    case_ids = [summary.case_master_id]
    accused = container.cases.accused_for_cases(case_ids)
    victims = container.cases.victims_for_cases(case_ids)
    act_sections = container.cases.act_sections_for_cases(case_ids)
    arrests = container.cases.arrests_for_cases(case_ids)
    priority = container.priority.for_case(summary.case_master_id)

    sections: list[tuple[str, list[str]]] = [
        ("Case particulars", [
            f"CrimeNo: {summary.crime_no}",
            f"Registered: {summary.crime_registered_date or 'not recorded'}",
            f"Police station: {summary.police_station_name or 'not recorded'}",
            f"District: {summary.district_name or 'not recorded'}",
            f"Classification: {summary.crime_sub_head or 'unclassified'} "
            f"under {summary.crime_head or 'no major head'}",
            f"Status: {summary.status or 'not recorded'}",
            f"Court: {summary.court_name or 'not committed to a court'}",
        ]),
    ]
    if act_sections:
        sections.append(("Charges", [
            f"{row.get('ShortName') or row.get('ActCode')} section {row.get('SectionCode')}"
            for row in act_sections
        ]))
    if summary.brief_facts:
        sections.append(("Recorded facts", [summary.brief_facts.strip()]))
    if victims:
        sections.append(("Victims", [
            f"{v.name}" + (f", age {v.age_year}" if v.age_year else "") for v in victims
        ]))
    sections.append(("Accused", [
        f"{a.person_ref or ''} {a.name}".strip() + (f", age {a.age_year}" if a.age_year else "")
        for a in accused
    ] or ["No accused named on this FIR."]))
    sections.append(("Arrests and surrenders", [
        f"{str(row.get('ArrestSurrenderDate'))[:10]} — {row.get('AccusedName') or 'accused'}"
        for row in arrests
    ] or ["No arrest or surrender recorded."]))
    if priority:
        items = priority["components"].get("items", [])
        sections.append(("Investigation priority indicator", [
            f"Score {float(priority['score']):.0f} of 100 ({priority['band']})",
            *[f"{item['name']}: {item['value']} contributes {item['weight']} points "
              f"({item['rationale']})" for item in items],
            "Formula: sum of component weights, capped at 100.",
        ]))
    return sections
