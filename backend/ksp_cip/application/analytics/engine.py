"""Deterministic analytics: trends, hotspots, early warning, sociology,
and the Investigation Priority Indicator.

None of these functions touch an LLM. Each returns both the numbers and a
:class:`ComputationTrace` describing exactly how they were produced, which is
what the console renders under "how this was calculated".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Sequence

from ...domain.models import ComputationTrace, UnitScope
from ...domain.value_objects import GeoPoint
from ...infrastructure.db.repositories import AggregateFilter, AnalyticsRepository, ReferenceRepository
from . import stats

BASELINE_MONTHS = 12
EARLY_WARNING_WINDOW_DAYS = 30
PRIORITY_BANDS = ((70.0, "high"), (45.0, "medium"), (0.0, "routine"))
OFFENDER_BANDS = ((70.0, "high"), (45.0, "medium"), (0.0, "low"))
SEASONALITY_COMPARISON_YEARS = 3
#: Below this many prior-year observations, a calendar bucket's deviation is
#: not reported as a finding — it would be indistinguishable from noise.
SEASONALITY_MIN_PRIOR_YEARS = 2
SOCIOLOGY_SUPPRESSION_THRESHOLD = 10
_MONTH_LABELS = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}


# ------------------------------------------------------------------ results


@dataclass(slots=True)
class TrendResult:
    periods: list[str]
    counts: list[int]
    total: int
    slope_per_month: float
    direction: str
    latest_period: str | None
    latest_count: int
    previous_count: int
    change_percent: float | None
    year_on_year_percent: float | None
    breakdown: list[dict[str, Any]]
    case_ids: list[int]
    crime_nos: list[str]
    trace: ComputationTrace


@dataclass(slots=True)
class HotspotCell:
    cell_id: str
    grid_row: int
    grid_col: int
    centroid_lat: float
    centroid_lon: float
    district_id: int | None
    unit_id: int | None
    case_count: int
    baseline_mean: float
    intensity: float
    top_crime_sub_head: str | None
    case_ids: list[int]
    window_start: str
    window_end: str


@dataclass(slots=True)
class HotspotResult:
    cells: list[HotspotCell]
    grid_metres: int
    window_days: int
    total_cases_considered: int
    trace: ComputationTrace


@dataclass(slots=True)
class EarlyWarningAlert:
    alert_id: str
    scope_type: str
    scope_id: int
    scope_name: str
    crime_sub_head_id: int | None
    crime_sub_head: str | None
    window_start: str
    window_end: str
    observed_count: int
    baseline_mean: float
    baseline_stddev: float
    z_score: float
    severity: str
    case_ids: list[int]
    explanation: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SeasonalBucket:
    key: str
    label: str
    current_period: str | None
    current_count: int
    baseline_years: list[int]
    baseline_mean: float
    baseline_stddev: float
    deviation_percent: float | None
    z_score: float | None
    insufficient_history: bool
    case_ids: list[int]


@dataclass(slots=True)
class SeasonalityResult:
    grouping: str
    comparison_years: int
    buckets: list[SeasonalBucket]
    total_periods_considered: int
    trace: ComputationTrace


@dataclass(slots=True)
class EventComparisonResult:
    event_id: str
    event_name: str
    event_type: str
    window_start: str
    window_end: str
    window_days: int
    observed_count: int
    comparison_windows: list[dict[str, Any]]
    comparison_mean: float
    comparison_stddev: float
    difference_percent: float | None
    z_score: float | None
    sample_size: int
    sufficient_evidence: bool
    case_ids: list[int]
    trace: ComputationTrace


@dataclass(slots=True)
class SociologyResult:
    dimension: str
    subject: str
    rows: list[dict[str, Any]]
    total_records: int
    top_associations: list[dict[str, Any]]
    suppressed_group_count: int
    suppressed_record_count: int
    suppression_threshold: int
    case_ids: list[int]
    trace: ComputationTrace


# ------------------------------------------------------------------- engine


class AnalyticsEngine:
    def __init__(
        self,
        analytics: AnalyticsRepository,
        reference: ReferenceRepository,
        *,
        hotspot_grid_metres: int = 750,
        hotspot_min_cases: int = 5,
        early_warning_sigma: float = 2.0,
        early_warning_min_baseline: float = 1.0,
    ) -> None:
        self._analytics = analytics
        self._reference = reference
        self._grid_metres = hotspot_grid_metres
        self._hotspot_min_cases = hotspot_min_cases
        self._sigma = early_warning_sigma
        self._min_baseline = early_warning_min_baseline

    @property
    def hotspot_min_cases(self) -> int:
        return self._hotspot_min_cases

    @property
    def hotspot_grid_metres(self) -> int:
        return self._grid_metres

    @property
    def early_warning_sigma(self) -> float:
        return self._sigma

    # ------------------------------------------------------------- trends
    def trend(
        self,
        filters: AggregateFilter,
        scope: UnitScope,
        *,
        breakdown_limit: int = 8,
    ) -> TrendResult:
        rows = self._analytics.monthly_counts(filters, scope)
        counts_by_period = {str(row["period"]): int(row["case_count"]) for row in rows if row["period"]}
        start = filters.date_from or _parse_period_start(min(counts_by_period, default=None))
        end = filters.date_to or _parse_period_end(max(counts_by_period, default=None))
        if start is None or end is None:
            empty_trace = ComputationTrace(
                operation="trend",
                description="No registered cases matched the filter, so no series could be built.",
                inputs=_filter_inputs(filters),
                row_count=0,
            )
            return TrendResult([], [], 0, 0.0, "no data", None, 0, 0, None, None, [], [], [], empty_trace)

        periods = stats.month_periods(start, end)
        series = stats.densify(counts_by_period, periods)
        labels = [key for key, _ in series]
        values = [value for _, value in series]
        slope, _intercept = stats.linear_trend([float(v) for v in values])
        latest = values[-1] if values else 0
        previous = values[-2] if len(values) > 1 else 0
        year_ago = values[-13] if len(values) >= 13 else None

        breakdown_rows = self._analytics.counts_by_sub_head(filters, scope, limit=breakdown_limit)
        id_rows = self._analytics.case_ids_for(filters, scope, limit=500)

        direction = "rising" if slope > 0.15 else "falling" if slope < -0.15 else "broadly flat"
        trace = ComputationTrace(
            operation="trend",
            description=(
                f"Counted {sum(values)} rows in curated_CaseMaster grouped by month of CrimeRegisteredDate "
                f"across {len(labels)} month(s), then fitted an ordinary least-squares line to the monthly counts."
            ),
            inputs=_filter_inputs(filters),
            row_count=sum(values),
            formula="slope = Σ(x−x̄)(y−ȳ) / Σ(x−x̄)²  over monthly counts",
            components=[{"period": label, "cases": value} for label, value in series],
        )
        return TrendResult(
            periods=labels,
            counts=values,
            total=sum(values),
            slope_per_month=round(slope, 3),
            direction=direction,
            latest_period=labels[-1] if labels else None,
            latest_count=latest,
            previous_count=previous,
            change_percent=_round_optional(stats.percent_change(latest, previous)),
            year_on_year_percent=_round_optional(
                stats.percent_change(latest, year_ago) if year_ago is not None else None
            ),
            breakdown=[
                {
                    "sub_head_id": row.get("sub_head_id"),
                    "sub_head": row.get("sub_head") or "unclassified",
                    "crime_head": row.get("crime_head"),
                    "case_count": int(row["case_count"]),
                }
                for row in breakdown_rows
            ],
            case_ids=[int(row["case_master_id"]) for row in id_rows],
            crime_nos=[str(row["crime_no"]) for row in id_rows],
            trace=trace,
        )

    # ----------------------------------------------------------- hotspots
    def hotspots(
        self,
        filters: AggregateFilter,
        scope: UnitScope,
        *,
        window_days: int = 90,
        limit: int = 20,
        as_of: date | None = None,
    ) -> HotspotResult:
        as_of = as_of or date.today()
        window_filters = AggregateFilter(
            unit_ids=filters.unit_ids,
            district_ids=filters.district_ids,
            crime_sub_head_ids=filters.crime_sub_head_ids,
            crime_head_ids=filters.crime_head_ids,
            date_from=filters.date_from or (as_of - timedelta(days=window_days)),
            date_to=filters.date_to or as_of,
        )
        points = self._analytics.geo_points(window_filters, scope)
        buckets: dict[tuple[int, int], dict[str, Any]] = {}
        for row in points:
            latitude, longitude = row.get("latitude"), row.get("longitude")
            if latitude is None or longitude is None:
                continue
            cell = GeoPoint(float(latitude), float(longitude)).grid_cell(self._grid_metres)
            bucket = buckets.setdefault(
                cell,
                {"lat_sum": 0.0, "lon_sum": 0.0, "case_ids": [], "sub_heads": {},
                 "district_id": row.get("district_id"), "unit_id": row.get("unit_id")},
            )
            bucket["lat_sum"] += float(latitude)
            bucket["lon_sum"] += float(longitude)
            bucket["case_ids"].append(int(row["case_master_id"]))
            sub_head = row.get("sub_head") or "unclassified"
            bucket["sub_heads"][sub_head] = bucket["sub_heads"].get(sub_head, 0) + 1

        occupancies = [len(bucket["case_ids"]) for bucket in buckets.values()]
        baseline = stats.mean([float(v) for v in occupancies]) if occupancies else 0.0

        cells: list[HotspotCell] = []
        for (row_id, col_id), bucket in buckets.items():
            count = len(bucket["case_ids"])
            if count < self._hotspot_min_cases:
                continue
            top_sub_head = max(bucket["sub_heads"], key=lambda k: bucket["sub_heads"][k]) if bucket["sub_heads"] else None
            cells.append(
                HotspotCell(
                    cell_id=f"{row_id}:{col_id}",
                    grid_row=row_id,
                    grid_col=col_id,
                    centroid_lat=round(bucket["lat_sum"] / count, 6),
                    centroid_lon=round(bucket["lon_sum"] / count, 6),
                    district_id=bucket["district_id"],
                    unit_id=bucket["unit_id"],
                    case_count=count,
                    baseline_mean=round(baseline, 3),
                    intensity=round(count / baseline, 3) if baseline else float(count),
                    top_crime_sub_head=top_sub_head,
                    case_ids=sorted(bucket["case_ids"]),
                    window_start=window_filters.date_from.isoformat() if window_filters.date_from else "",
                    window_end=window_filters.date_to.isoformat() if window_filters.date_to else "",
                )
            )
        cells.sort(key=lambda c: (c.case_count, c.intensity), reverse=True)
        cells = cells[:limit]

        trace = ComputationTrace(
            operation="hotspot",
            description=(
                f"Binned {len(points)} geocoded case locations into ~{self._grid_metres} m grid cells over "
                f"{window_days} days, kept cells with at least {self._hotspot_min_cases} cases, and expressed "
                "intensity as the cell's count divided by the mean count across occupied cells."
            ),
            inputs={**_filter_inputs(window_filters), "grid_metres": self._grid_metres,
                    "min_cases": self._hotspot_min_cases},
            row_count=len(points),
            formula="intensity = cell_case_count / mean(case_count over occupied cells)",
        )
        return HotspotResult(cells=cells, grid_metres=self._grid_metres, window_days=window_days,
                             total_cases_considered=len(points), trace=trace)

    # ------------------------------------------------------- early warning
    def early_warning(
        self,
        scope: UnitScope,
        *,
        as_of: date | None = None,
        window_days: int = EARLY_WARNING_WINDOW_DAYS,
        district_ids: Sequence[int] | None = None,
        max_alerts: int = 25,
    ) -> list[EarlyWarningAlert]:
        """Flag (district × crime sub-head) combinations whose trailing-window
        count is anomalously high against their own 12-month baseline.

        The comparison is per-combination, so a busy district does not
        permanently alarm and a quiet one is not permanently silent.
        """
        as_of = as_of or date.today()
        window_start = as_of - timedelta(days=window_days)
        baseline_start = as_of - timedelta(days=30 * BASELINE_MONTHS)
        filters = AggregateFilter(district_ids=district_ids, date_from=baseline_start, date_to=as_of)
        rows = self._analytics.monthly_counts_by_sub_head(filters, scope)

        # Build district-level series by joining unit → district via reference.
        district_rows = self._analytics.counts_by_district(filters, scope)
        district_names = {int(r["district_id"]): r.get("district_name") or "unknown"
                          for r in district_rows if r.get("district_id") is not None}

        alerts: list[EarlyWarningAlert] = []
        for district_id, district_name in district_names.items():
            district_filter = AggregateFilter(district_ids=[district_id], date_from=baseline_start, date_to=as_of)
            sub_rows = self._analytics.monthly_counts_by_sub_head(district_filter, scope)
            by_sub: dict[tuple[int | None, str], dict[str, int]] = {}
            for row in sub_rows:
                key = (row.get("sub_head_id"), row.get("sub_head") or "unclassified")
                by_sub.setdefault(key, {})[str(row["period"])] = int(row["case_count"])

            for (sub_head_id, sub_head), period_counts in by_sub.items():
                periods = stats.month_periods(baseline_start, as_of)
                series = [count for _key, count in stats.densify(period_counts, periods)]
                if len(series) < 4:
                    continue
                observed_window = AggregateFilter(
                    district_ids=[district_id],
                    crime_sub_head_ids=[sub_head_id] if sub_head_id is not None else None,
                    date_from=window_start,
                    date_to=as_of,
                )
                observed = self._analytics.total_cases(observed_window, scope)
                baseline_series = [float(v) for v in series[:-1]]
                baseline_mean = stats.mean(baseline_series)
                baseline_sigma = stats.sample_stddev(baseline_series)
                if baseline_mean < self._min_baseline and observed < self._hotspot_min_cases:
                    continue
                score = stats.z_score(float(observed), baseline_series, floor=self._min_baseline)
                if score < self._sigma:
                    continue
                case_rows = self._analytics.case_ids_for(observed_window, scope, limit=100)
                severity = "high" if score >= self._sigma * 1.75 else "elevated"
                alert_id = hashlib.blake2s(
                    f"{district_id}:{sub_head_id}:{window_start}:{as_of}".encode(), digest_size=8
                ).hexdigest()
                alerts.append(
                    EarlyWarningAlert(
                        alert_id=alert_id,
                        scope_type="district",
                        scope_id=district_id,
                        scope_name=district_name,
                        crime_sub_head_id=sub_head_id,
                        crime_sub_head=sub_head,
                        window_start=window_start.isoformat(),
                        window_end=as_of.isoformat(),
                        observed_count=observed,
                        baseline_mean=round(baseline_mean, 3),
                        baseline_stddev=round(baseline_sigma, 3),
                        z_score=round(score, 3),
                        severity=severity,
                        case_ids=[int(r["case_master_id"]) for r in case_rows],
                        explanation={
                            "method": "trailing-window count vs 12-month monthly baseline (z-score)",
                            "window_days": window_days,
                            "baseline_months": BASELINE_MONTHS,
                            "sigma_threshold": self._sigma,
                            "monthly_baseline_series": series[:-1],
                            "formula": "z = (observed − mean(baseline)) / max(stddev(baseline), floor)",
                            "floor": self._min_baseline,
                        },
                    )
                )
        alerts.sort(key=lambda a: a.z_score, reverse=True)
        return alerts[:max_alerts]

    # -------------------------------------------------------- seasonality
    def seasonality(
        self,
        filters: AggregateFilter,
        scope: UnitScope,
        *,
        comparison_years: int = SEASONALITY_COMPARISON_YEARS,
        grouping: str = "month",
    ) -> SeasonalityResult:
        """Compare each calendar month's most recent count against that same
        month's history in prior years.

        This is deliberately not the same computation as :meth:`trend`. Trend
        asks "is the series rising"; this asks "is a given calendar period
        running hot or cold relative to what *that period* has historically
        looked like" (festival-month spikes, monsoon patterns). A bucket with
        fewer than :data:`SEASONALITY_MIN_PRIOR_YEARS` distinct prior years is
        marked ``insufficient_history`` rather than given a deviation figure,
        because a one-year "baseline" is not a baseline.
        """
        if grouping != "month":
            raise ValueError("Only calendar-month grouping is supported currently")

        rows = self._analytics.monthly_counts(filters, scope)
        counts_by_period = {str(row["period"]): int(row["case_count"]) for row in rows if row["period"]}
        if not counts_by_period:
            empty_trace = ComputationTrace(
                operation="seasonality",
                description="No registered cases matched the filter, so no calendar pattern could be compared.",
                inputs=_filter_inputs(filters),
                row_count=0,
            )
            return SeasonalityResult(
                grouping=grouping, comparison_years=comparison_years, buckets=[],
                total_periods_considered=0, trace=empty_trace,
            )

        by_month: dict[str, list[tuple[int, int]]] = {}
        for period, count in counts_by_period.items():
            year_s, month_s = period.split("-")[:2]
            by_month.setdefault(month_s, []).append((int(year_s), count))

        buckets: list[SeasonalBucket] = []
        for month_key in sorted(by_month):
            entries = sorted(by_month[month_key], key=lambda e: e[0])
            latest_year = entries[-1][0]
            current_count = sum(c for y, c in entries if y == latest_year)
            prior_years = sorted({y for y, _ in entries if y != latest_year}, reverse=True)[:comparison_years]
            history = [c for y, c in entries if y in prior_years]
            insufficient = len(prior_years) < SEASONALITY_MIN_PRIOR_YEARS
            baseline_mean = stats.mean([float(v) for v in history]) if history else 0.0
            baseline_sigma = stats.sample_stddev([float(v) for v in history]) if history else 0.0
            deviation = None if insufficient else _round_optional(stats.percent_change(current_count, baseline_mean))
            z = None if insufficient else round(stats.z_score(float(current_count), [float(v) for v in history]), 3)

            month_start = date(latest_year, int(month_key), 1)
            month_end = _parse_period_end(f"{latest_year}-{month_key}") or month_start
            month_filter = AggregateFilter(
                unit_ids=filters.unit_ids, district_ids=filters.district_ids,
                crime_sub_head_ids=filters.crime_sub_head_ids, crime_head_ids=filters.crime_head_ids,
                date_from=month_start, date_to=month_end,
            )
            case_rows = self._analytics.case_ids_for(month_filter, scope, limit=100)

            buckets.append(SeasonalBucket(
                key=month_key,
                label=_MONTH_LABELS[int(month_key)],
                current_period=f"{latest_year}-{month_key}",
                current_count=current_count,
                baseline_years=prior_years,
                baseline_mean=round(baseline_mean, 3),
                baseline_stddev=round(baseline_sigma, 3),
                deviation_percent=deviation,
                z_score=z,
                insufficient_history=insufficient,
                case_ids=[int(r["case_master_id"]) for r in case_rows],
            ))

        trace = ComputationTrace(
            operation="seasonality",
            description=(
                f"Grouped {sum(counts_by_period.values())} registered cases by calendar month across "
                f"{len({y for month in by_month.values() for y, _ in month})} distinct year(s), then compared each "
                f"month's most recent count to the mean of up to {comparison_years} prior year(s) of that same "
                "calendar month."
            ),
            inputs={**_filter_inputs(filters), "comparison_years": comparison_years, "grouping": grouping},
            row_count=sum(counts_by_period.values()),
            formula="z = (current − mean(prior years of same month)) / max(stddev(prior years), 1.0)",
        )
        return SeasonalityResult(
            grouping=grouping, comparison_years=comparison_years, buckets=buckets,
            total_periods_considered=len(counts_by_period), trace=trace,
        )

    # ---------------------------------------------------- event comparison
    def event_comparison(
        self,
        filters: AggregateFilter,
        scope: UnitScope,
        *,
        event: dict[str, Any],
        comparison_window_count: int = 4,
    ) -> EventComparisonResult:
        """Compare an event window against matched non-event windows.

        The matching is deliberately simple and stated rather than clever: the
        same number of days, immediately preceding the event window, repeated
        ``comparison_window_count`` times and skipping the event window itself.
        Equal-length adjacent windows control for the obvious confounders
        (season, reporting practice) without pretending to be a causal design.

        **This measures coincidence, never cause.** The result deliberately
        carries no field a caller could read as causal, and the agent renders
        it as "was elevated during", per implementationv2 §9.2.
        """
        window_start = date.fromisoformat(str(event["date_from"])[:10])
        window_end = date.fromisoformat(str(event["date_to"])[:10])
        window_days = max(1, (window_end - window_start).days + 1)

        observed = self._analytics.counts_between(
            filters, scope, date_from=window_start, date_to=window_end
        )

        comparison_windows: list[dict[str, Any]] = []
        cursor_end = window_start - timedelta(days=1)
        for _ in range(comparison_window_count):
            cursor_start = cursor_end - timedelta(days=window_days - 1)
            count = self._analytics.counts_between(
                filters, scope, date_from=cursor_start, date_to=cursor_end
            )
            comparison_windows.append({
                "start": cursor_start.isoformat(),
                "end": cursor_end.isoformat(),
                "count": count,
            })
            cursor_end = cursor_start - timedelta(days=1)

        counts = [float(w["count"]) for w in comparison_windows]
        comparison_mean = stats.mean(counts)
        comparison_sigma = stats.sample_stddev(counts)
        # With too few comparison windows, or none carrying any cases, a
        # percentage difference would be arithmetic theatre.
        sufficient = len(counts) >= 2 and sum(counts) > 0
        difference = _round_optional(stats.percent_change(observed, comparison_mean)) if sufficient else None
        z = round(stats.z_score(float(observed), counts), 3) if sufficient else None

        window_filter = AggregateFilter(
            unit_ids=filters.unit_ids, district_ids=filters.district_ids,
            crime_sub_head_ids=filters.crime_sub_head_ids, crime_head_ids=filters.crime_head_ids,
            date_from=window_start, date_to=window_end,
        )
        case_rows = self._analytics.case_ids_for(window_filter, scope, limit=100)

        trace = ComputationTrace(
            operation="event_comparison",
            description=(
                f"Counted cases in the {window_days}-day window of '{event.get('event_name')}' "
                f"({window_start} to {window_end}) and compared them with {len(comparison_windows)} "
                f"immediately preceding {window_days}-day window(s) under the same filters. "
                "This measures whether counts were elevated during the window, not whether the "
                "event caused them."
            ),
            inputs={**_filter_inputs(filters), "event_id": event.get("event_id"),
                    "window_days": window_days, "comparison_windows": len(comparison_windows)},
            row_count=observed,
            formula="z = (observed − mean(matched windows)) / max(stddev(matched windows), 1.0)",
            components=comparison_windows,
        )
        return EventComparisonResult(
            event_id=str(event.get("event_id") or ""),
            event_name=str(event.get("event_name") or "unnamed event"),
            event_type=str(event.get("event_type") or "unspecified"),
            window_start=window_start.isoformat(),
            window_end=window_end.isoformat(),
            window_days=window_days,
            observed_count=observed,
            comparison_windows=comparison_windows,
            comparison_mean=round(comparison_mean, 3),
            comparison_stddev=round(comparison_sigma, 3),
            difference_percent=difference,
            z_score=z,
            sample_size=len(comparison_windows),
            sufficient_evidence=sufficient,
            case_ids=[int(r["case_master_id"]) for r in case_rows],
            trace=trace,
        )

    # --------------------------------------------------------- sociology
    def sociology(
        self,
        filters: AggregateFilter,
        scope: UnitScope,
        *,
        dimension: str,
        subject: str = "complainant",
        top_n: int = 10,
        suppression_threshold: int = SOCIOLOGY_SUPPRESSION_THRESHOLD,
    ) -> SociologyResult:
        if subject == "victim":
            rows = self._analytics.victim_demographic_dimension(filters, scope, dimension=dimension)
        else:
            rows = self._analytics.complainant_demographics(filters, scope, dimension=dimension)
        total = sum(int(row["record_count"]) for row in rows)
        by_value: dict[str, int] = {}
        for row in rows:
            key = str(row.get("dimension_value") or "unknown")
            by_value[key] = by_value.get(key, 0) + int(row["record_count"])

        # Small-cell suppression: a demographic group with very few records is
        # not reported individually, so a rare (dimension value × district)
        # combination cannot be used to re-identify a specific complainant.
        visible = {k: v for k, v in by_value.items() if v >= suppression_threshold}
        suppressed = {k: v for k, v in by_value.items() if v < suppression_threshold}

        associations: list[dict[str, Any]] = []
        for value, count in stats.top_n([(k, float(v)) for k, v in visible.items()], top_n):
            sub_heads = [
                {"sub_head": row.get("sub_head") or "unclassified", "count": int(row["record_count"])}
                for row in rows
                if str(row.get("dimension_value") or "unknown") == value
            ]
            sub_heads.sort(key=lambda item: item["count"], reverse=True)
            associations.append(
                {
                    "value": value,
                    "records": int(count),
                    "share_percent": round(stats.share(count, total), 2),
                    "top_crime_sub_heads": sub_heads[:3],
                    "suppressed": False,
                }
            )
        suppressed_records = sum(suppressed.values())
        if suppressed:
            associations.append({
                "value": f"{len(suppressed)} smaller group(s), each under {suppression_threshold} records",
                "records": suppressed_records,
                "share_percent": round(stats.share(suppressed_records, total), 2),
                "top_crime_sub_heads": [],
                "suppressed": True,
            })

        case_rows = self._analytics.case_ids_for(filters, scope, limit=300)
        trace = ComputationTrace(
            operation="sociology",
            description=(
                f"Cross-tabulated {total} {subject} records by {dimension} against crime sub-head using "
                f"GROUP BY over curated_{'Victim' if subject == 'victim' else 'ComplainantDetails'} joined to "
                "curated_CaseMaster. Counts are of recorded records, not of population. Groups under "
                f"{suppression_threshold} records are merged rather than shown individually."
            ),
            inputs={**_filter_inputs(filters), "dimension": dimension, "subject": subject,
                    "suppression_threshold": suppression_threshold},
            row_count=total,
        )
        return SociologyResult(
            dimension=dimension,
            subject=subject,
            rows=rows,
            total_records=total,
            top_associations=associations,
            suppressed_group_count=len(suppressed),
            suppressed_record_count=suppressed_records,
            suppression_threshold=suppression_threshold,
            case_ids=[int(r["case_master_id"]) for r in case_rows],
            trace=trace,
        )

    # ---------------------------------------------- priority indicator (IPI)
    def investigation_priority(
        self,
        *,
        case: dict[str, Any],
        days_open: int | None,
        arrest_count: int,
        chargesheet_filed: bool,
        accused_count: int,
        network_size: int,
        max_offender_score: float,
        alert_linked: bool,
    ) -> dict[str, Any]:
        """Transparent weighted-sum Investigation Priority Indicator.

        It scores the *case*, never a person. Every component and weight is
        returned so the officer can see and dispute the arithmetic.
        """
        gravity = str(case.get("gravity") or "").lower()
        gravity_weight = 30.0 if "heinous" in gravity else 14.0 if "grave" in gravity else 6.0
        age_weight = 0.0
        if days_open is not None and not chargesheet_filed:
            age_weight = min(20.0, days_open / 90.0 * 20.0)
        progress_weight = 0.0 if chargesheet_filed else (6.0 if arrest_count else 14.0)
        network_weight = min(16.0, network_size * 2.0)
        offender_weight = min(14.0, max_offender_score * 0.14)
        alert_weight = 6.0 if alert_linked else 0.0
        multi_accused_weight = min(6.0, max(0, accused_count - 1) * 2.0)

        components = [
            {"name": "offence gravity", "value": gravity or "unknown", "weight": round(gravity_weight, 2),
             "rationale": "Heinous offences carry statutory supervision expectations."},
            {"name": "days pending without chargesheet", "value": days_open if not chargesheet_filed else 0,
             "weight": round(age_weight, 2), "rationale": "Capped at 20 points after ~90 days."},
            {"name": "investigation progress", "value": "chargesheeted" if chargesheet_filed
             else ("arrest made" if arrest_count else "no arrest"), "weight": round(progress_weight, 2),
             "rationale": "A case with no arrest and no chargesheet needs attention first."},
            {"name": "linked network size", "value": network_size, "weight": round(network_weight, 2),
             "rationale": "2 points per connected person, capped at 16."},
            {"name": "highest linked offender score", "value": round(max_offender_score, 2),
             "weight": round(offender_weight, 2), "rationale": "14% of the offender score, capped at 14."},
            {"name": "part of an active early-warning cluster", "value": alert_linked,
             "weight": round(alert_weight, 2), "rationale": "Series cases benefit from joint investigation."},
            {"name": "multiple accused", "value": accused_count, "weight": round(multi_accused_weight, 2),
             "rationale": "2 points per additional accused, capped at 6."},
        ]
        score = sum(item["weight"] for item in components)
        return {
            "score": round(min(score, 100.0), 2),
            "band": stats.band_for(score, PRIORITY_BANDS),
            "components": {
                "items": components,
                "formula": "score = Σ component weights, capped at 100",
                "max_possible": 100.0,
            },
        }

    def priority_for_summary(
        self,
        summary: Any,
        *,
        accused_count: int = 0,
        as_of: date | None = None,
        arrest_count: int = 0,
        chargesheet_filed: bool = False,
        network_size: int = 0,
        max_offender_score: float = 0.0,
        alert_linked: bool = False,
    ) -> dict[str, Any] | None:
        """Convenience wrapper over :meth:`investigation_priority` for a CaseSummary.

        Returns ``None`` when the case carries no registration date, since
        "days pending" is then undefined and a score would be a guess.
        """
        registered = getattr(summary, "crime_registered_date", None)
        if registered is None:
            return None
        reference = as_of or date.today()
        return self.investigation_priority(
            case={"gravity": getattr(summary, "gravity", None)},
            days_open=max(0, (reference - registered).days),
            arrest_count=arrest_count,
            chargesheet_filed=chargesheet_filed,
            accused_count=accused_count,
            network_size=network_size,
            max_offender_score=max_offender_score,
            alert_linked=alert_linked,
        )


# ----------------------------------------------------------------- helpers


def _filter_inputs(filters: AggregateFilter) -> dict[str, Any]:
    return {
        "unit_ids": list(filters.unit_ids or []),
        "district_ids": list(filters.district_ids or []),
        "crime_sub_head_ids": list(filters.crime_sub_head_ids or []),
        "crime_head_ids": list(filters.crime_head_ids or []),
        "date_from": filters.date_from.isoformat() if filters.date_from else None,
        "date_to": filters.date_to.isoformat() if filters.date_to else None,
    }


def _parse_period_start(period: str | None) -> date | None:
    if not period:
        return None
    year, month = period.split("-")[:2]
    return date(int(year), int(month), 1)


def _parse_period_end(period: str | None) -> date | None:
    start = _parse_period_start(period)
    if start is None:
        return None
    if start.month == 12:
        return date(start.year, 12, 31)
    return date(start.year, start.month + 1, 1) - timedelta(days=1)


def _round_optional(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None
