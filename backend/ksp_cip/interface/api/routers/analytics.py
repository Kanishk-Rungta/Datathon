"""Deterministic analytics endpoints. Each response carries its own trace."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from ....domain.enums import Permission
from ....infrastructure.db.repositories import AggregateFilter
from ....domain.models import Principal
from ..deps import ContainerDep, PrincipalDep, require, scope_note
from ..schemas import HotspotRequest, SociologyRequest, TrendRequest

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _trace(trace: Any) -> dict[str, Any]:
    return {
        "operation": trace.operation, "description": trace.description,
        "inputs": trace.inputs, "row_count": trace.row_count,
        "formula": trace.formula, "components": trace.components,
    }


@router.post("/trend")
def trend(
    payload: TrendRequest,
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.READ_AGGREGATES)),
) -> dict[str, Any]:
    filters = AggregateFilter(
        district_ids=payload.district_ids or None,
        unit_ids=payload.unit_ids or None,
        crime_sub_head_ids=payload.crime_sub_head_ids or None,
        date_from=payload.date_from,
        date_to=payload.date_to,
    )
    result = container.engine.trend(filters, principal.scope)
    return {
        "total": result.total,
        "periods": result.periods,
        "counts": result.counts,
        "slope_per_month": result.slope_per_month,
        "direction": result.direction,
        "latest_period": result.latest_period,
        "latest_count": result.latest_count,
        "change_percent": result.change_percent,
        "year_on_year_percent": result.year_on_year_percent,
        "breakdown": result.breakdown,
        "trace": _trace(result.trace),
        "scope_note": scope_note(principal),
        "caveat": (
            "Counts are of FIRs registered. They reflect reporting and registration practice as "
            "well as underlying crime."
        ),
    }


@router.post("/hotspots")
def hotspots(
    payload: HotspotRequest,
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.READ_AGGREGATES)),
) -> dict[str, Any]:
    filters = AggregateFilter(
        district_ids=payload.district_ids or None,
        crime_sub_head_ids=payload.crime_sub_head_ids or None,
    )
    result = container.engine.hotspots(filters, principal.scope, window_days=payload.window_days)
    return {
        "window_days": result.window_days,
        "grid_metres": result.grid_metres,
        "total_cases_considered": result.total_cases_considered,
        "cells": [
            {
                "cell_id": cell.cell_id, "lat": cell.centroid_lat, "lon": cell.centroid_lon,
                "district_id": cell.district_id, "case_count": cell.case_count,
                "intensity": cell.intensity, "top_crime_sub_head": cell.top_crime_sub_head,
                "case_master_ids": cell.case_ids[:100],
            }
            for cell in result.cells
        ],
        "trace": _trace(result.trace),
        "scope_note": scope_note(principal),
        "caveat": (
            "Grid binning approximates kernel density estimation. A cell boundary can split one "
            "real concentration into two."
        ),
    }


@router.get("/early-warning")
def early_warning(
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.READ_AGGREGATES)),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    district_ids = None
    stored = container.alerts.alerts(district_ids=district_ids, limit=limit)
    visible = [
        alert for alert in stored
        if principal.scope.statewide or _alert_in_scope(container, principal, alert)
    ]
    return {
        "alerts": visible,
        "scope_note": scope_note(principal),
        "method": (
            "For each district and crime type, the trailing 30-day count is compared against the "
            "mean and standard deviation of that combination's own preceding monthly counts."
        ),
        "formula": "z = (observed - mean(baseline)) / max(stddev(baseline), 1.0)",
        "caveat": (
            "A z-score flags a departure from a unit's own recent history. It is a prompt to look, "
            "not a finding that crime has risen."
        ),
    }


def _alert_in_scope(container: Any, principal: Any, alert: dict[str, Any]) -> bool:
    if alert.get("scope_type") != "district":
        return True
    unit_ids = container.reference.unit_ids_for_district(int(alert["scope_id"]))
    return bool(unit_ids & set(principal.scope.unit_ids))


@router.post("/sociology")
def sociology(
    payload: SociologyRequest,
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.READ_AGGREGATES)),
) -> dict[str, Any]:
    container.authorization.assert_aggregate_only_dimension(principal, payload.dimension)
    filters = AggregateFilter(
        district_ids=payload.district_ids or None,
        date_from=payload.date_from,
        date_to=payload.date_to,
    )
    result = container.engine.sociology(filters, principal.scope, dimension=payload.dimension)
    return {
        "dimension": result.dimension,
        "total_records": result.total_records,
        "associations": result.top_associations,
        "trace": _trace(result.trace),
        "scope_note": scope_note(principal),
        "caveat": (
            "These are counts of recorded complaints, not rates against population. Reporting "
            "propensity, policing intensity and base rates all affect them. Association is not "
            "causation and must not be read as a statement about any community."
        ),
    }


@router.get("/summary")
def summary(
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.READ_AGGREGATES)),
) -> dict[str, Any]:
    """Dashboard header figures for the console."""
    filters = AggregateFilter()
    result = container.engine.trend(filters, principal.scope)
    return {
        "total_cases": result.total,
        "latest_period": result.latest_period,
        "latest_count": result.latest_count,
        "direction": result.direction,
        "change_percent": result.change_percent,
        "top_crime_types": result.breakdown[:5],
        "active_alerts": len(container.alerts.alerts(limit=50)),
        "hotspot_cells": len(container.hotspots.cells(limit=50)),
        "scope_note": scope_note(principal),
    }
