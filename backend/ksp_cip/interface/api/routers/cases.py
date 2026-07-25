"""Structured case access. Every response is scope-filtered in SQL."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from ....domain.enums import Permission
from ....domain.errors import NotFoundError
from ....infrastructure.db.repositories import CaseFilter
from ....domain.models import Principal
from ..deps import ContainerDep, PrincipalDep, require, scope_note
from ..schemas import CaseSearchRequest

router = APIRouter(prefix="/cases", tags=["cases"])


@router.post("/search")
def search(
    payload: CaseSearchRequest,
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.READ_CASE_DETAIL)),
) -> dict[str, Any]:
    filters = CaseFilter(
        district_ids=payload.district_ids or None,
        unit_ids=payload.unit_ids or None,
        crime_sub_head_ids=payload.crime_sub_head_ids or None,
        status_ids=payload.status_ids or None,
        crime_nos=payload.crime_nos or None,
        date_from=payload.date_from,
        date_to=payload.date_to,
        limit=payload.limit,
        offset=payload.offset,
    )
    results = container.cases.search(filters, principal.scope)
    total = container.cases.count(filters, principal.scope)
    return {
        "total": total,
        "returned": len(results),
        "scope_note": scope_note(principal),
        "cases": [case.model_dump(mode="json") for case in results],
    }


@router.get("/{crime_no}")
def case_detail(
    crime_no: str,
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.READ_CASE_DETAIL)),
) -> dict[str, Any]:
    summary = container.cases.by_crime_no(crime_no, principal.scope)
    if summary is None:
        raise NotFoundError(
            "No FIR with that CrimeNo is available within your authorized scope.",
            crime_no=crime_no,
        )
    case_ids = [summary.case_master_id]
    include_sensitive = principal.has(Permission.READ_SENSITIVE_DEMOGRAPHICS)
    container.audit.record(
        action="case.read", principal=principal, object_type="case",
        object_ids=[summary.crime_no], outcome="success",
    )
    return {
        "case": summary.model_dump(mode="json"),
        "complainants": [
            p.model_dump(mode="json")
            for p in container.cases.complainants_for_cases(case_ids, include_sensitive=include_sensitive)
        ],
        "victims": [p.model_dump(mode="json") for p in container.cases.victims_for_cases(case_ids)],
        "accused": [p.model_dump(mode="json") for p in container.cases.accused_for_cases(case_ids)],
        "act_sections": container.cases.act_sections_for_cases(case_ids),
        "arrests": container.cases.arrests_for_cases(case_ids),
        "chargesheets": container.cases.chargesheets_for_cases(case_ids),
        "officer": container.cases.officer_for_case(summary.case_master_id),
        "priority": container.priority.for_case(summary.case_master_id),
        "sensitive_fields_masked": not include_sensitive,
    }


@router.get("/{crime_no}/similar")
def similar(
    crime_no: str,
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.READ_CASE_DETAIL)),
    limit: int = Query(default=8, ge=1, le=25),
) -> dict[str, Any]:
    summary = container.cases.by_crime_no(crime_no, principal.scope)
    if summary is None:
        raise NotFoundError("No FIR with that CrimeNo is available within your authorized scope.",
                            crime_no=crime_no)
    documents = container.retrieval.similar_to_case(summary.case_master_id, principal.scope, top_k=limit)
    cases = {c.case_master_id: c for c in
             container.cases.by_ids([d.case_master_id for d in documents], principal.scope)}
    return {
        "anchor": summary.model_dump(mode="json"),
        "method": (
            f"Embedded the anchor FIR's recorded facts with {container.retrieval.model_name}, "
            "restricted candidates to your authorized units, then ranked by cosine similarity."
        ),
        "results": [
            {
                "case": cases[d.case_master_id].model_dump(mode="json"),
                "similarity": d.similarity,
                "matched_text": d.text_snippet[:280],
            }
            for d in documents if d.case_master_id in cases
        ],
    }


@router.get("/reference/masters")
def masters(container: ContainerDep, principal: PrincipalDep) -> dict[str, Any]:
    """Lookup lists for the console's filter controls."""
    return {
        "districts": container.reference.districts(),
        "crime_heads": container.reference.crime_heads(),
        "crime_sub_heads": container.reference.crime_sub_heads(),
        "case_statuses": container.reference.case_statuses(),
        "gravity_levels": container.reference.gravity_levels(),
        "units": [
            unit for unit in container.reference.units()
            if principal.scope.statewide or int(unit["UnitID"]) in principal.scope.unit_ids
        ],
    }
