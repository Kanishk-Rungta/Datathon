"""Administration: seeding, pipeline control, data quality and the audit trail."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from ....domain.enums import Permission
from ....domain.models import Principal
from ..deps import ContainerDep, PrincipalDep, require
from ..schemas import SeedRequest

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/seed")
def seed(
    payload: SeedRequest,
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.ADMIN_PIPELINE)),
) -> dict[str, Any]:
    summary = container.seeder.run(
        target_cases=payload.target_cases, months=payload.months, reset=payload.reset
    )
    container.audit.record(
        action="admin.seed", principal=principal, object_type="pipeline",
        object_ids=["seed"], outcome="success",
        detail={"cases": summary["generated_cases"], "reset": payload.reset},
    )
    container.warm()
    return summary


@router.post("/refresh")
def refresh(
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.ADMIN_PIPELINE)),
) -> dict[str, Any]:
    """Re-run entity resolution, graph build, embeddings, hotspots, alerts and priority."""
    report = container.refresher.refresh_all(transactions=container.financial.all_transactions())
    container.audit.record(
        action="admin.refresh", principal=principal, object_type="pipeline",
        object_ids=["intelligence_refresh"], outcome="success", detail=report.as_dict(),
    )
    container.warm()
    return report.as_dict()


@router.get("/pipeline")
def pipeline_status(
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.ADMIN_PIPELINE)),
) -> dict[str, Any]:
    return {
        "batches": container.control.batches(limit=40),
        "watermarks": container.control.watermarks(),
        "row_counts": {
            table: container.control.store_row_count(table)
            for table in (
                "curated_CaseMaster", "curated_Accused", "curated_Victim",
                "curated_ComplainantDetails", "curated_ArrestSurrender",
                "cip_graph_edge", "cip_person_identity", "cip_embedding_index",
                "cip_case_priority", "ext_financial_transaction",
            )
        },
        "graph": container.graph.stats(),
    }


@router.get("/data-quality")
def data_quality(
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.ADMIN_PIPELINE)),
    limit: int = Query(default=60, ge=1, le=300),
) -> dict[str, Any]:
    return {
        "results": container.control.dq_results(limit=limit),
        "summary": container.control.dq_summary(),
        "note": (
            "Findings are recorded, never silently corrected. A blocking failure stops the load; "
            "warnings are surfaced here and affect how confidently the platform answers."
        ),
    }


@router.get("/audit")
def audit(
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.READ_AUDIT)),
    actor_user_id: str | None = None,
    action: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if actor_user_id:
        filters["actor_user_id"] = actor_user_id
    if action:
        filters["action"] = action
    return {
        "events": container.audit_repository.query(filters, limit=limit),
        "stats": container.audit_repository.stats(),
        "retention_days": container.settings.audit_retention_days,
        "note": "The audit log is append-only. Entries are never edited or deleted by the platform.",
    }


@router.get("/llm-usage")
def llm_usage(
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.ADMIN_PIPELINE)),
) -> dict[str, Any]:
    return {
        "provider": str(container.settings.llm_provider),
        "is_local_deterministic": container.llm.is_local,
        "usage": container.llm.usage(),
        "note": (
            "The model is used for intent disambiguation and phrasing only. Every figure and "
            "citation in an answer is produced by deterministic code, and any model rewrite is "
            "verified against the original before it is shown."
        ),
    }


@router.post("/purge-expired")
def purge_expired(
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.ADMIN_PIPELINE)),
) -> dict[str, Any]:
    """Apply conversation retention. Audit records are deliberately untouched."""
    result = container.memory.purge_expired()
    container.audit.record(
        action="admin.purge_expired", principal=principal, object_type="conversation",
        outcome="success", detail=result,
    )
    return result
