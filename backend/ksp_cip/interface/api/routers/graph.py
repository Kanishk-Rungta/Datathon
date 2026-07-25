"""Link analysis endpoints.

Every edge returned here is derived rather than recorded, so every response
says so explicitly and each link carries the basis on which it was inferred.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from ....application.graph import person_node
from ....domain.enums import Permission
from ....domain.errors import NotFoundError, ValidationError
from ....domain.models import Principal
from ..deps import ContainerDep, PrincipalDep, require, scope_note
from ..schemas import GraphExpandRequest, GraphPathRequest, ReviewDecisionRequest

router = APIRouter(prefix="/graph", tags=["graph"])

INFERENCE_NOTICE = (
    "Links are derived from shared FIR records, not from any confirmed association. "
    "Co-accused on one FIR does not establish an ongoing relationship."
)


def _resolve_person(container: Any, name: str) -> dict[str, Any] | None:
    target = name.casefold()
    candidates = container.identities.identities()
    exact = [i for i in candidates if str(i["canonical_name"]).casefold() == target]
    pool = exact or [i for i in candidates if target in str(i["canonical_name"]).casefold()]
    return max(pool, key=lambda i: len(i.get("case_ids", []))) if pool else None


@router.post("/expand")
def expand(
    payload: GraphExpandRequest,
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.USE_GRAPH_TOOLS)),
) -> dict[str, Any]:
    if payload.node_id:
        seed = payload.node_id
    elif payload.person_name:
        identity = _resolve_person(container, payload.person_name)
        if identity is None:
            raise NotFoundError("That person does not appear in the indexed accused records.",
                                name=payload.person_name)
        seed = person_node(str(identity["identity_id"]))
    elif payload.case_master_id:
        seed = f"case:{payload.case_master_id}"
    else:
        raise ValidationError("Provide node_id, person_name or case_master_id.")

    result = container.graph.expand(
        seed, principal.scope, hops=payload.hops,
        edge_types=payload.edge_types or None, max_nodes=payload.max_nodes,
    )
    view = result.view
    container.audit.record(
        action="graph.expand", principal=principal, object_type="graph",
        object_ids=[seed], outcome="success",
        detail={"hops": payload.hops, "nodes": len(view.nodes)},
    )
    return {
        "seed": seed,
        "nodes": [
            {
                "id": node.node_id, "label": node.label, "type": str(node.node_type),
                "community": view.communities.get(node.node_id, 0),
                "centrality": view.centrality.get(node.node_id, 0.0),
                "is_seed": node.node_id == seed,
            }
            for node in view.nodes
        ],
        "links": [
            {
                "source": link.source, "target": link.target, "type": str(link.edge_type),
                "weight": link.weight, "case_master_ids": link.case_master_ids[:20],
                "inferred": True,
            }
            for link in view.links
        ],
        "edge_type_counts": result.edge_type_counts,
        "withheld_by_scope": result.trimmed_by_scope,
        "scope_note": scope_note(principal),
        "notice": INFERENCE_NOTICE,
    }


@router.post("/path")
def path(
    payload: GraphPathRequest,
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.USE_GRAPH_TOOLS)),
) -> dict[str, Any]:
    left = _resolve_person(container, payload.from_person)
    right = _resolve_person(container, payload.to_person)
    missing = [n for n, f in ((payload.from_person, left), (payload.to_person, right)) if f is None]
    if missing:
        raise NotFoundError("Not present in the indexed accused records.", names=missing)
    result = container.graph.shortest_path(
        person_node(str(left["identity_id"])), person_node(str(right["identity_id"])), principal.scope
    )
    return {
        **result,
        "from": left["canonical_name"],
        "to": right["canonical_name"],
        "notice": INFERENCE_NOTICE,
        "scope_note": scope_note(principal),
    }


@router.get("/communities")
def communities(
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.USE_GRAPH_TOOLS)),
    limit: int = Query(default=8, ge=1, le=40),
) -> dict[str, Any]:
    return {
        "communities": container.graph.top_communities(limit=limit),
        "method": (
            "Louvain modularity optimisation with a fixed seed over the weighted person link graph, "
            "with members ranked by degree centrality."
        ),
        "notice": (
            "Clusters indicate where shared records concentrate. They do not establish that a group "
            "is organised."
        ),
    }


@router.get("/offenders")
def offenders(
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.USE_OFFENDER_PROFILING)),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    unit_ids = None if principal.scope.statewide else sorted(principal.scope.unit_ids)
    records = container.identities.top_offenders(limit=limit, unit_ids=unit_ids)
    return {
        "offenders": records,
        "scope_note": scope_note(principal),
        "method": (
            "A fixed, published weighted sum over recorded case count, distinct offence types, "
            "recency, gravity escalation and link-graph centrality."
        ),
        "notice": (
            "This score summarises what is already on record. It is not a prediction about future "
            "behaviour and must not be used as one."
        ),
    }


@router.get("/entity-resolution/review")
def review_queue(
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.USE_GRAPH_TOOLS)),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Pairs that scored between the two thresholds and await a human decision."""
    return {
        "pending": container.identities.review_queue(limit=limit),
        "stats": container.identities.link_stats(),
        "thresholds": {
            "auto_link_at_or_above": container.settings.entity_resolution_tau_high,
            "review_between": [
                container.settings.entity_resolution_tau_low,
                container.settings.entity_resolution_tau_high,
            ],
        },
        "notice": (
            "Nothing here has been merged. Records above the upper threshold are linked "
            "automatically and remain individually addressable; these fall between the thresholds "
            "and need a human decision."
        ),
    }


@router.post("/entity-resolution/review")
def resolve_review(
    payload: ReviewDecisionRequest,
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.USE_GRAPH_TOOLS)),
) -> dict[str, Any]:
    container.identities.resolve_link(
        payload.link_id, state=payload.decision, reviewer=principal.user_id
    )
    container.audit.record(
        action="entity_resolution.review", principal=principal, object_type="er_link",
        object_ids=[payload.link_id], outcome="success",
        detail={"decision": payload.decision},
    )
    return {"link_id": payload.link_id, "decision": payload.decision, "recorded_by": principal.username}


@router.get("/financial/{identity_id}")
def financial(
    identity_id: str,
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.USE_FINANCIAL_TOOLS)),
) -> dict[str, Any]:
    identity = container.identities.identity(identity_id)
    if identity is None:
        raise NotFoundError("Unknown identity.", identity_id=identity_id)
    refs = [str(a) for a in identity.get("source_ids", [])]
    transactions = container.financial.for_refs(refs)
    from ....application.graph import FinancialAnalyzer

    summary = FinancialAnalyzer().summarize(
        subject_ref=refs[0] if refs else "",
        subject_label=str(identity["canonical_name"]),
        transactions=transactions,
    )
    return {
        "subject": summary.subject_label,
        "total_sent": summary.total_sent,
        "total_received": summary.total_received,
        "counterparties": [
            {"label": f.label, "kind": f.kind, "sent": f.sent, "received": f.received,
             "net": f.net, "transactions": f.txn_count}
            for f in summary.counterparties
        ],
        "patterns": summary.patterns,
        "case_master_ids": summary.case_ids,
        "is_synthetic_extension": True,
        "notice": (
            "Financial records are a clearly marked synthetic extension. The organiser's FIR schema "
            "contains no transaction data, so nothing here reflects real banking activity."
        ),
    }
