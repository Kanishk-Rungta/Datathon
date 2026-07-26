"""Deterministic analytics endpoints. Each response carries its own trace."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from ....domain.enums import Permission
from ....infrastructure.db.repositories import AggregateFilter
from ....domain.models import Principal
from ..deps import ContainerDep, PrincipalDep, require, scope_note
from ..schemas import (
    EventComparisonRequest,
    HotspotRequest,
    SeasonalityRequest,
    SociologyRequest,
    TrendRequest,
)

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
    result = container.engine.sociology(
        filters, principal.scope, dimension=payload.dimension, subject=payload.subject
    )
    return {
        "dimension": result.dimension,
        "subject": result.subject,
        "total_records": result.total_records,
        "associations": result.top_associations,
        "suppressed_group_count": result.suppressed_group_count,
        "suppressed_record_count": result.suppressed_record_count,
        "suppression_threshold": result.suppression_threshold,
        "trace": _trace(result.trace),
        "scope_note": scope_note(principal),
        "caveat": (
            f"These are counts of recorded {payload.subject}s, not rates against population. Reporting "
            "propensity, policing intensity and base rates all affect them. Association is not "
            "causation and must not be read as a statement about any community. Groups under the "
            "suppression threshold are merged, not shown individually."
        ),
    }


@router.post("/seasonality")
def seasonality(
    payload: SeasonalityRequest,
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
    result = container.engine.seasonality(filters, principal.scope)
    return {
        "grouping": result.grouping,
        "comparison_years": result.comparison_years,
        "total_periods_considered": result.total_periods_considered,
        "buckets": [
            {
                "key": bucket.key, "label": bucket.label, "current_period": bucket.current_period,
                "current_count": bucket.current_count, "baseline_years": bucket.baseline_years,
                "baseline_mean": bucket.baseline_mean, "baseline_stddev": bucket.baseline_stddev,
                "deviation_percent": bucket.deviation_percent, "z_score": bucket.z_score,
                "insufficient_history": bucket.insufficient_history,
                "case_master_ids": bucket.case_ids[:100],
            }
            for bucket in result.buckets
        ],
        "trace": _trace(result.trace),
        "scope_note": scope_note(principal),
        "caveat": (
            "This compares a calendar month's most recent count against that same month in prior "
            "years. A month with fewer than 2 prior years of history is marked insufficient rather "
            "than given a deviation figure. This is a historical comparison, not a forecast."
        ),
    }


@router.get("/events")
def events(
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.READ_AGGREGATES)),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Approved reference events available for window comparison.

    Only approved rows are returned; an unreviewed event must not reach an
    answer (implementationv2 §9.2).
    """
    rows = container.events.approved_events(limit=limit)
    return {
        "events": rows,
        "count": len(rows),
        "note": (
            "Events are CIP reference data, not part of the FIR schema. Only rows approved by a "
            "governance owner are listed."
        ),
    }


@router.post("/event-comparison")
def event_comparison(
    payload: EventComparisonRequest,
    container: ContainerDep,
    principal: Principal = Depends(require(Permission.READ_AGGREGATES)),
) -> dict[str, Any]:
    from ....domain.errors import NotFoundError, ValidationError

    if not payload.event_id and not payload.event_name:
        raise ValidationError("Either event_id or event_name is required")

    event = None
    if payload.event_name:
        event = container.events.by_name(payload.event_name)
    if event is None and payload.event_id:
        event = next(
            (row for row in container.events.approved_events(limit=500)
             if str(row.get("event_id")) == payload.event_id),
            None,
        )
    if event is None:
        raise NotFoundError(
            "No approved event matches that identifier",
            event=payload.event_id or payload.event_name,
        )

    filters = AggregateFilter(
        district_ids=payload.district_ids or None,
        crime_sub_head_ids=payload.crime_sub_head_ids or None,
    )
    result = container.engine.event_comparison(
        filters, principal.scope, event=event,
        comparison_window_count=payload.comparison_window_count,
    )
    return {
        "event_id": result.event_id,
        "event_name": result.event_name,
        "event_type": result.event_type,
        "window": {"start": result.window_start, "end": result.window_end, "days": result.window_days},
        "observed_count": result.observed_count,
        "comparison_windows": result.comparison_windows,
        "comparison_mean": result.comparison_mean,
        "comparison_stddev": result.comparison_stddev,
        "difference_percent": result.difference_percent,
        "z_score": result.z_score,
        "sample_size": result.sample_size,
        "sufficient_evidence": result.sufficient_evidence,
        "case_master_ids": result.case_ids[:100],
        "trace": _trace(result.trace),
        "scope_note": scope_note(principal),
        "caveat": (
            "This compares recorded counts during the event window with matched windows immediately "
            "before it. It shows whether counts were elevated during the window; it does not show "
            "that the event caused them."
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
