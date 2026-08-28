"""CrimeAnalyticsAgent — trends, hotspots, early warning, sociological insight.

Every figure this agent reports comes from :class:`AnalyticsEngine`, which is
pure arithmetic over ``GROUP BY`` results. The agent's job is to turn those
numbers into evidence-bound claims and a chart specification, and to attach
the caveats that make the numbers safe to act on.
"""

from __future__ import annotations

from typing import Any

from ...domain.enums import AgentName, Intent, Permission, Provenance
from ...domain.models import AgentResult, StructuredPayload
from ...infrastructure.db.repositories import (
    AggregateFilter,
    AlertRepository,
    AnalyticsRepository,
    HotspotRepository,
    ReferenceRepository,
)
from ..analytics import AnalyticsEngine
from ..nlu import default_date_window
from ..services.audit import AuditService, audited
from ..services.authorization import AuthorizationService
from ..services.evidence import aggregate_evidence, alert_evidence, claim, empty_result_evidence, trace
from .base import INDIVIDUAL_PREDICTION_RE, AgentRequest, BaseAgent

SOCIOLOGY_DIMENSIONS = ("occupation", "age_band", "gender", "religion", "caste")
#: The organiser's Victim table has no occupation/religion/caste columns —
#: a subject-selector request for those against victims is honestly
#: substituted, not silently dropped or rejected.
VICTIM_DIMENSIONS = ("gender", "age_band")
SEASONAL_LOOKBACK_MONTHS = 48
#: The forecast reads as much history as it can get: a backtest needs months
#: the method was not shown, so a short window would starve the very check that
#: decides whether the projection is worth publishing.
FORECAST_LOOKBACK_MONTHS = 48



class CrimeAnalyticsAgent(BaseAgent):
    name = AgentName.CRIME_ANALYTICS

    def __init__(
        self,
        audit: AuditService,
        engine: AnalyticsEngine,
        analytics: AnalyticsRepository,
        reference: ReferenceRepository,
        hotspots: HotspotRepository,
        alerts: AlertRepository,
        authorization: AuthorizationService,
        correlator: Any | None = None,
        spatiotemporal_forecaster: Any | None = None,
        graph: Any | None = None,
    ) -> None:
        super().__init__(audit)
        self._engine = engine
        self._analytics = analytics
        self._reference = reference
        self._hotspots = hotspots
        self._alerts = alerts
        self._authorization = authorization
        self._correlator = correlator
        self._spatiotemporal_forecaster = spatiotemporal_forecaster
        self._graph = graph

    @audited("agent.crime_analytics", object_type="aggregate")
    def handle(self, request: AgentRequest) -> AgentResult:
        request.principal.require(Permission.READ_AGGREGATES)
        if request.intent is Intent.HOTSPOT_QUERY:
            return self._hotspot(request)
        if request.intent is Intent.EARLY_WARNING:
            return self._early_warning(request)
        if request.intent is Intent.DEMOGRAPHIC_INSIGHT:
            return self._sociology(request)
        if request.intent is Intent.SOCIOECONOMIC_QUERY:
            return self._socioeconomic_correlation(request)
        if request.intent is Intent.SEASONAL_QUERY:
            return self._seasonal(request)
        if request.intent is Intent.FORECAST_QUERY:
            return self._forecast(request)
        if request.intent is Intent.SPATIOTEMPORAL_QUERY:
            return self._spatiotemporal(request)
        return self._trend(request)

    # ----------------------------------------------------- spatial forecast
    def _spatiotemporal(self, request: AgentRequest) -> AgentResult:
        """Where recorded incidents are projected to concentrate next.

        Distinct from :meth:`_hotspot`, which describes concentration that has
        *already* been recorded, and from :meth:`_forecast`, which projects a
        count over time with no geography. This answers "where next", and is
        labelled a projection everywhere it surfaces.

        Aggregate by construction: grid cells, never people. The same
        individual-prediction refusal the supervisor applies before routing
        covers this path too.
        """
        if self._spatiotemporal_forecaster is None:  # pragma: no cover - wiring guard
            return self.empty_result(
                request, "Spatial forecasting is not enabled on this deployment."
            )

        filters = AggregateFilter(
            unit_ids=request.slots.unit_ids or None,
            district_ids=request.slots.district_ids or None,
            crime_sub_head_ids=request.slots.crime_sub_head_ids or None,
        )
        horizon_days = int(request.options.get("horizon_days", 30))
        result = self._spatiotemporal_forecaster.predict(
            filters, request.scope, horizon_days=horizon_days, as_of=request.today,
        )
        scope_label = self._scope_label(request)

        if not result.predicted_cells:
            nothing = empty_result_evidence(
                key=f"spatiotemporal:none:{result.window_start}:{result.window_end}",
                label=f"No geo-coded cases {scope_label} in the training window",
                detail={"window": [result.window_start, result.window_end]},
            )
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(
                    f"No geo-coded recorded cases {scope_label} fall in the training window "
                    f"({result.window_start} to {result.window_end}), so there is no spatial "
                    "pattern to project from.",
                    [nothing], provenance=Provenance.DETERMINISTIC_COMPUTATION,
                )],
                evidence=[nothing], traces=[result.trace], confidence=0.85,
            )

        caveat = (
            "This projects where recorded incidents have concentrated, forward. It is a planning "
            "aid, not a prediction of specific crimes and not a statement about any individual. "
            "Grid cells are an approximation: a cell boundary can split one real concentration in "
            "two, and a projection cannot anticipate an event it has never seen."
        )
        window = aggregate_evidence(
            key=f"spatiotemporal:{result.window_start}:{result.window_end}",
            label=(
                f"{result.total_historical_cases} recorded case(s) {scope_label} between "
                f"{result.window_start} and {result.window_end}"
            ),
            case_master_ids=[],
            detail={"grid_metres": result.grid_metres, "model": result.model_name,
                    "horizon_days": result.horizon_days},
        )
        evidence_items = [window]

        claims = [claim(
            f"Projected concentration {scope_label} over the next {result.horizon_days} day(s), "
            f"across {len(result.predicted_cells)} grid cell(s) of {result.grid_metres} m, from "
            f"{result.total_historical_cases} recorded case(s).",
            evidence_items, provenance=Provenance.DETERMINISTIC_COMPUTATION,
        )]
        for cell in result.predicted_cells[:5]:
            claims.append(claim(
                f"{cell.top_crime_sub_head or 'Mixed crime types'} near "
                f"{cell.centroid_lat:.4f}, {cell.centroid_lon:.4f}: about "
                f"{cell.expected_count:.1f} incident(s) expected (range {cell.lower_bound:.1f}–"
                f"{cell.upper_bound:.1f}), against {cell.historical_count} recorded — "
                f"{cell.risk_level} risk band.",
                evidence_items, provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        claims.append(claim(caveat, evidence_items, provenance=Provenance.DETERMINISTIC_COMPUTATION))

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=evidence_items,
            traces=[result.trace], confidence=0.8,
            payload=StructuredPayload(
                payload_type="spatiotemporal_forecast",
                title=f"Projected concentration {scope_label} (projection, not observed)",
                # Keys mirror POST /analytics/spatiotemporal-forecast exactly, so
                # the SpatioTemporalForecast component renders either source.
                data={
                    "horizon_days": result.horizon_days,
                    "grid_metres": result.grid_metres,
                    "window_start": result.window_start,
                    "window_end": result.window_end,
                    "total_historical_cases": result.total_historical_cases,
                    "projected_total_cases": result.projected_total_cases,
                    "model_name": result.model_name,
                    "predicted_cells": [
                        {
                            "cell_id": c.cell_id,
                            "lat": c.centroid_lat,
                            "lon": c.centroid_lon,
                            "district_id": c.district_id,
                            "historical_count": c.historical_count,
                            "expected_count": c.expected_count,
                            "lower_bound": c.lower_bound,
                            "upper_bound": c.upper_bound,
                            "hotspot_probability": c.hotspot_probability,
                            "risk_level": c.risk_level,
                            "top_crime_sub_head": c.top_crime_sub_head,
                        }
                        for c in result.predicted_cells
                    ],
                    "caveat": caveat,
                },
            ),
            warnings=["A projection is not a recorded fact."],
        )

    # ------------------------------------------------------------ forecast
    def _forecast(self, request: AgentRequest) -> AgentResult:
        """An aggregate planning projection.

        Two properties are enforced here rather than left to prose:

        * the answer is always a **range** with its method and backtest error
          attached, never a bare number; and
        * when the history cannot support a projection, the agent says so and
          returns no figure — an evidenced refusal, not a guess.

        Nothing on this path accepts or emits an individual. Forecasting *a
        named person's* future offending is refused by design (ADR-0006), and
        the evaluation corpus asserts it stays refused.
        """
        names = request.slots.person_names or request.pinned_person_names
        if names or INDIVIDUAL_PREDICTION_RE.search(request.text_english or ""):
            refusal = empty_result_evidence(
                key="forecast:individual-prediction-refused",
                label="Forecasting an individual's future offending is not a capability of this platform",
                detail={"requested_scope": "individual", "supported_scope": "area and crime type"},
            )
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[
                    claim(
                        "This platform does not forecast whether a particular person will offend, and "
                        "will not estimate that. Recorded history can be summarised for a named person, "
                        "but it is a record of what happened, never a prediction of what they will do.",
                        [refusal], provenance=Provenance.DETERMINISTIC_COMPUTATION,
                    ),
                    claim(
                        "Projections are available for an area and crime type — for example "
                        "\"project theft cases in Mysuru for the next quarter\".",
                        [refusal], provenance=Provenance.DETERMINISTIC_COMPUTATION,
                    ),
                ],
                evidence=[refusal],
                traces=[trace(
                    "forecast_refused",
                    "The request asked for a forward-looking statement about an individual. Forecasting "
                    "is restricted to aggregate area × crime-type counts by design; no individual "
                    "projection was computed.",
                    inputs={"scope_requested": "individual"}, row_count=0,
                )],
                confidence=0.9,
                warnings=["Individual future-offending prediction is not supported."],
            )

        start, end = default_date_window(request.slots, today=request.today, months=FORECAST_LOOKBACK_MONTHS)
        filters = AggregateFilter(
            unit_ids=request.slots.unit_ids or None,
            district_ids=request.slots.district_ids or None,
            crime_sub_head_ids=request.slots.crime_sub_head_ids or None,
            date_from=start,
            date_to=end,
        )
        horizon = int(request.options.get("horizon_months", 3))
        result = self._engine.forecast(filters, request.scope, horizon_months=horizon)
        scope_label = self._scope_label(request)

        if result.insufficient_history:
            nothing = empty_result_evidence(
                key=f"forecast:insufficient:{start}:{end}",
                label=(
                    f"Only {result.history_months} month(s) of recorded history {scope_label} — "
                    "too few to project from"
                ),
                detail={"window": [str(start), str(end)],
                        "history_months": result.history_months},
            )
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[
                    claim(
                        f"There is not enough recorded history {scope_label} to project forward: "
                        f"{result.history_months} month(s) are available. No projection is offered, "
                        "because a figure produced from this much history would look authoritative "
                        "while meaning nothing.",
                        [nothing], provenance=Provenance.DETERMINISTIC_COMPUTATION,
                    ),
                ],
                evidence=[nothing], traces=[result.trace], confidence=0.85,
                warnings=["Insufficient history for a projection."],
            )

        window = aggregate_evidence(
            key=f"forecast:{result.method}:{start}:{end}",
            label=(
                f"{result.history_months} month(s) of recorded cases {scope_label}, "
                f"averaging {result.observed_mean:.1f} per month"
            ),
            # The observed cases the projection rests on, so a reader can open
            # the records rather than take the number on trust.
            case_master_ids=result.case_ids,
            detail={
                "history_months": result.history_months,
                "observed_mean": result.observed_mean,
                "method": result.method,
                "backtest": [
                    {"method": m.method, "mean_absolute_error": m.mean_absolute_error,
                     "origins_tested": m.origins_tested,
                     "beat_constant_baseline": m.beat_constant_baseline}
                    for m in result.backtests
                ],
            },
        )
        evidence_items = [window]

        # Quote the recent level, not only the long-run average: on a series
        # that has shifted, "averaging 0.8 a month" beside a projection of 7
        # reads as a contradiction rather than as a trend.
        level = (
            f"averaging {result.observed_mean:.1f} case(s) a month overall and "
            f"{result.recent_mean:.1f} over the most recent months"
            if abs(result.recent_mean - result.observed_mean) >= 1.0
            else f"averaging {result.observed_mean:.1f} case(s) a month"
        )
        claims = [claim(
            f"Projected registered-case counts {scope_label} for the next {result.horizon_months} month(s), "
            f"from {result.history_months} month(s) of recorded history, {level}.",
            evidence_items, provenance=Provenance.DETERMINISTIC_COMPUTATION,
        )]
        for point in result.points:
            claims.append(claim(
                f"{point.period}: about {point.expected:.0f} case(s), likely between "
                f"{point.lower:.0f} and {point.upper:.0f}.",
                evidence_items, provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        claims.append(claim(
            f"Method: {result.method}. {result.method_reason}.",
            evidence_items, provenance=Provenance.DETERMINISTIC_COMPUTATION,
        ))
        # The caveat is a claim, not a footnote, so it survives LLM polish and
        # the PDF export the same way every other sentence does. It carries the
        # window evidence because the sparse variant quotes the observed mean,
        # and a claim containing a digit needs a locator like any other.
        claims.append(claim(
            result.caveat, evidence_items, provenance=Provenance.DETERMINISTIC_COMPUTATION,
        ))

        warnings = ["A projection is not a recorded fact."]
        if result.sparse:
            warnings.append("Low monthly counts — read the range, not the midpoint.")
        if not any(m.beat_constant_baseline for m in result.backtests):
            warnings.append("The method did not beat a long-run average on this series.")

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=evidence_items,
            traces=[result.trace], confidence=0.8,
            payload=StructuredPayload(
                payload_type="forecast",
                title=f"Projected case counts {scope_label} (projection, not observed)",
                # Its own payload type rather than a "line" chart carrying an
                # is_forecast flag: a projection and a record of what happened
                # should not be one rendering path distinguished by a boolean.
                data={
                    "points": [
                        {"period": p.period, "expected": p.expected,
                         "lower": p.lower, "upper": p.upper}
                        for p in result.points
                    ],
                    "method": result.method,
                    "method_reason": result.method_reason,
                    "history_months": result.history_months,
                    "observed_mean": result.observed_mean,
                    "recent_mean": result.recent_mean,
                    "sparse": result.sparse,
                    "backtests": [
                        {"method": m.method, "mean_absolute_error": m.mean_absolute_error,
                         "origins_tested": m.origins_tested,
                         "beat_constant_baseline": m.beat_constant_baseline}
                        for m in result.backtests
                    ],
                    "caveat": result.caveat,
                },
            ),
            warnings=warnings,
            data={"method": result.method, "horizon_months": result.horizon_months},
        )

    # -------------------------------------------------------------- trends
    def _trend(self, request: AgentRequest) -> AgentResult:
        start, end = default_date_window(request.slots, today=request.today, months=12)
        filters = AggregateFilter(
            unit_ids=request.slots.unit_ids or None,
            district_ids=request.slots.district_ids or None,
            crime_sub_head_ids=request.slots.crime_sub_head_ids or None,
            date_from=start,
            date_to=end,
        )
        result = self._engine.trend(filters, request.scope)
        if result.total == 0:
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim("No registered cases fall inside that filter, so there is no series to report.")],
                traces=[result.trace], confidence=0.9,
            )

        scope_label = self._scope_label(request)
        evidence = aggregate_evidence(
            key=f"trend:{start}:{end}:{'-'.join(map(str, request.slots.district_ids)) or 'all'}",
            label=f"{result.total} FIRs counted by month, {start} to {end}, {scope_label}",
            case_master_ids=result.case_ids,
            crime_nos=result.crime_nos,
            detail={"periods": result.periods, "counts": result.counts},
        )
        claims = [
            claim(
                f"{result.total} case(s) were registered {scope_label} between {start} and {end}.",
                [evidence], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ),
            claim(
                f"The monthly series is {result.direction} at {result.slope_per_month:+.2f} cases per month.",
                [evidence], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ),
        ]
        if result.latest_period:
            change = (
                f"{result.change_percent:+.1f}% against the previous month"
                if result.change_percent is not None else "no comparable previous month"
            )
            claims.append(claim(
                f"The most recent month, {result.latest_period}, recorded {result.latest_count} case(s) — {change}.",
                [evidence], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        if result.year_on_year_percent is not None:
            claims.append(claim(
                f"Year on year, that month is {result.year_on_year_percent:+.1f}% against the same month a year earlier.",
                [evidence], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        if result.breakdown:
            top = result.breakdown[0]
            claims.append(claim(
                f"The largest single category is {top['sub_head']} with {top['case_count']} case(s).",
                [evidence], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        claims.append(claim(
            "Counts are of FIRs registered, which reflects reporting and registration practice as well as "
            "underlying crime."
        ))

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=[evidence],
            traces=[result.trace],
            payload=StructuredPayload(
                payload_type="line",
                title=f"Monthly registered cases — {scope_label}",
                data={
                    "labels": result.periods,
                    "series": [{"name": "Registered cases", "values": result.counts}],
                    "breakdown": result.breakdown,
                },
            ),
            data={"case_master_ids": result.case_ids[:200], "trend": {
                "periods": result.periods, "counts": result.counts, "slope": result.slope_per_month}},
        )

    # ------------------------------------------------------------ hotspots
    def _hotspot(self, request: AgentRequest) -> AgentResult:
        window_days = request.slots.relative_period_days or 90
        filters = AggregateFilter(
            unit_ids=request.slots.unit_ids or None,
            district_ids=request.slots.district_ids or None,
            crime_sub_head_ids=request.slots.crime_sub_head_ids or None,
            date_from=request.slots.date_from,
            date_to=request.slots.date_to,
        )
        result = self._engine.hotspots(filters, request.scope, window_days=window_days, as_of=request.today)
        if not result.cells:
            nothing = empty_result_evidence(
                key=f"hotspot:none:{window_days}",
                label=f"No grid cell met the threshold in the last {window_days} days",
                detail={"window_days": window_days, "grid_metres": result.grid_metres,
                        "min_cases": self._engine.hotspot_min_cases},
            )
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(
                    f"No grid cell reached the reporting threshold in the last {window_days} days, so there is "
                    "no concentration worth flagging.",
                    [nothing], provenance=Provenance.DETERMINISTIC_COMPUTATION,
                )],
                evidence=[nothing], traces=[result.trace], confidence=0.85,
            )

        evidence_items = []
        claims = [claim(
            f"{len(result.cells)} location cluster(s) cleared the threshold across "
            f"{result.total_cases_considered} geocoded case(s) in the last {result.window_days} days.",
            provenance=Provenance.DETERMINISTIC_COMPUTATION,
        )]
        for index, cell in enumerate(result.cells[:5], start=1):
            item = aggregate_evidence(
                key=f"hotspot:{cell.cell_id}",
                label=f"Grid cell {cell.cell_id}: {cell.case_count} cases",
                case_master_ids=cell.case_ids,
                detail={"centroid": [cell.centroid_lat, cell.centroid_lon], "intensity": cell.intensity},
            )
            evidence_items.append(item)
            district = self._reference.district(cell.district_id) if cell.district_id else None
            claims.append(claim(
                f"Cluster {index}: {cell.case_count} case(s) around "
                f"{cell.centroid_lat:.4f}, {cell.centroid_lon:.4f}"
                + (f" in {district['DistrictName']}" if district else "")
                + f", {cell.intensity:.1f}× the average occupied cell"
                + (f", mostly {cell.top_crime_sub_head}" if cell.top_crime_sub_head else "")
                + ".",
                [item], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        claims[0].evidence_locators = [evidence_items[0].locator] if evidence_items else []
        claims.append(claim(
            "Grid binning is a coarse stand-in for kernel density estimation; a cell boundary can split one "
            "real concentration into two."
        ))

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=evidence_items,
            traces=[result.trace],
            payload=StructuredPayload(
                payload_type="map",
                title=f"Case concentrations, last {result.window_days} days",
                data={
                    "grid_metres": result.grid_metres,
                    "cells": [
                        {
                            "cell_id": cell.cell_id, "lat": cell.centroid_lat, "lon": cell.centroid_lon,
                            "case_count": cell.case_count, "intensity": cell.intensity,
                            "top_crime_sub_head": cell.top_crime_sub_head,
                            "case_master_ids": cell.case_ids[:50],
                        }
                        for cell in result.cells
                    ],
                },
            ),
            data={"case_master_ids": [cid for cell in result.cells for cid in cell.case_ids][:200]},
        )

    # -------------------------------------------------------- seasonality
    def _seasonal(self, request: AgentRequest) -> AgentResult:
        start, end = default_date_window(request.slots, today=request.today, months=SEASONAL_LOOKBACK_MONTHS)
        filters = AggregateFilter(
            unit_ids=request.slots.unit_ids or None,
            district_ids=request.slots.district_ids or None,
            crime_sub_head_ids=request.slots.crime_sub_head_ids or None,
            date_from=start,
            date_to=end,
        )
        result = self._engine.seasonality(filters, request.scope)
        scope_label = self._scope_label(request)

        if not result.buckets:
            nothing = empty_result_evidence(
                key=f"seasonality:none:{start}:{end}",
                label=f"No registered cases between {start} and {end} to compare by calendar month",
                detail={"window": [str(start), str(end)]},
            )
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(
                    "No registered cases fall inside that filter, so there is no calendar pattern to compare.",
                    [nothing], provenance=Provenance.DETERMINISTIC_COMPUTATION,
                )],
                evidence=[nothing], traces=[result.trace], confidence=0.85,
            )

        reportable = [b for b in result.buckets if not b.insufficient_history]
        if not reportable:
            nothing = empty_result_evidence(
                key=f"seasonality:insufficient:{start}:{end}",
                label=f"Fewer than 2 prior years of history for every calendar month {scope_label}",
                detail={"window": [str(start), str(end)], "months_seen": result.total_periods_considered},
            )
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(
                    f"The available history ({start} to {end}) does not yet cover enough separate years for any "
                    "calendar month to have a reliable seasonal baseline, so no seasonal finding is reported.",
                    [nothing], provenance=Provenance.DETERMINISTIC_COMPUTATION,
                )],
                evidence=[nothing], traces=[result.trace], confidence=0.85,
            )

        evidence_items = []
        claims = [claim(
            f"{len(reportable)} of {len(result.buckets)} calendar month(s) {scope_label} have enough history "
            "(at least 2 prior years) to compare against a seasonal baseline.",
            provenance=Provenance.DETERMINISTIC_COMPUTATION,
        )]
        ranked = sorted(reportable, key=lambda b: abs(b.z_score or 0.0), reverse=True)
        for bucket in ranked[:5]:
            item = aggregate_evidence(
                key=f"seasonality:{bucket.key}:{bucket.current_period}",
                label=f"{bucket.label} {bucket.current_period}: {bucket.current_count} case(s)",
                case_master_ids=bucket.case_ids,
                detail={"baseline_mean": bucket.baseline_mean, "baseline_years": bucket.baseline_years,
                        "z_score": bucket.z_score, "deviation_percent": bucket.deviation_percent},
            )
            evidence_items.append(item)
            if bucket.deviation_percent is None:
                change_text = f"against a baseline of {bucket.baseline_mean:.1f} in prior year(s)"
            else:
                direction = "above" if bucket.deviation_percent >= 0 else "below"
                change_text = f"{abs(bucket.deviation_percent):.1f}% {direction} its baseline of {bucket.baseline_mean:.1f}"
            claims.append(claim(
                f"{bucket.label} {bucket.current_period}: {bucket.current_count} case(s), {change_text} "
                f"(mean of {bucket.label} in {', '.join(map(str, bucket.baseline_years))}).",
                [item], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        if evidence_items:
            claims[0].evidence_locators = [evidence_items[0].locator]
        insufficient_months = [b.label for b in result.buckets if b.insufficient_history]
        if insufficient_months:
            claims.append(claim(
                f"{', '.join(insufficient_months)} do not yet have enough prior-year history and are excluded "
                "from this comparison."
            ))
        claims.append(claim(
            "These compare recorded FIR counts for a specific calendar month against that same month in prior "
            "years. This is a historical comparison, not a forecast of what will happen next time that month "
            "occurs."
        ))

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=evidence_items,
            traces=[result.trace],
            payload=StructuredPayload(
                payload_type="bar",
                title=f"Calendar-month pattern — {scope_label}",
                data={
                    "labels": [b.label for b in result.buckets],
                    "series": [{"name": "Most recent year", "values": [b.current_count for b in result.buckets]}],
                    "seasonal_detail": [
                        {
                            "month": b.label,
                            "current_period": b.current_period,
                            "current_count": b.current_count,
                            "baseline_mean": b.baseline_mean,
                            "deviation_percent": b.deviation_percent,
                            "z_score": b.z_score,
                            "insufficient_history": b.insufficient_history,
                        }
                        for b in result.buckets
                    ],
                },
            ),
            data={"case_master_ids": [cid for b in reportable for cid in b.case_ids][:200]},
        )

    # -------------------------------------------------------- early warning
    def _organised_activity(self, request: AgentRequest) -> list[dict[str, Any]]:
        """Recurring co-accused clusters, shaped as early-warning alerts.

        Brief §8 asks for gang and organised-crime warning. What the records
        support is *recurrence*: the same people named together across several
        FIRs, over a period, sometimes across districts. Whether that group is
        an organised criminal enterprise is a legal determination, so nothing
        here calls it one — the wording stays on what was recorded.
        """
        if self._graph is None:
            return []
        filters = AggregateFilter(
            unit_ids=request.slots.unit_ids or None,
            district_ids=request.slots.district_ids or None,
        )
        rows = self._analytics.case_time_and_place(filters, request.scope)
        case_dates = {int(r["case_master_id"]): str(r["registered_date"]) for r in rows
                      if r.get("registered_date")}
        case_districts = {int(r["case_master_id"]): int(r["district_id"]) for r in rows
                          if r.get("district_id") is not None}

        signals = self._graph.organised_activity(
            request.scope, case_dates=case_dates, case_districts=case_districts,
        )
        alerts: list[dict[str, Any]] = []
        for signal in signals:
            names = ", ".join(signal.member_labels[:4])
            if signal.size > 4:
                names += f" and {signal.size - 4} other(s)"
            reach = f"{signal.district_count} district(s)" if signal.district_count else "one area"
            alerts.append({
                "alert_id": f"ORG-{signal.community_id}",
                "scope_name": names,
                "crime_sub_head": "Recurring co-accused cluster",
                "observed_count": signal.shared_case_count,
                "baseline_mean": 1.0,
                "z_score": signal.score,
                "severity": signal.band,
                "case_ids": signal.case_ids,
                "window_start": signal.first_seen or "",
                "window_end": signal.last_seen or "",
                "organised": True,
                "explanation": {
                    "members": signal.size,
                    "shared_cases": signal.shared_case_count,
                    "districts": signal.district_count,
                    "span_days": signal.span_days,
                    "cohesion": signal.cohesion,
                    "link_types": signal.edge_types,
                    "score_weights": "shared cases 40 · cohesion 25 · districts 20 · span 15",
                },
                "description": (
                    f"{signal.size} people named together on {signal.shared_case_count} shared FIR(s) "
                    f"across {reach} over {signal.span_days} day(s). Link density {signal.cohesion:.2f}. "
                    "This is a recurring association in the recorded links — not a finding that an "
                    "organised group exists."
                ),
            })
        return alerts

    def _early_warning(self, request: AgentRequest) -> AgentResult:
        stored = self._alerts.alerts(district_ids=request.slots.district_ids or None, limit=10)
        if stored:
            alerts = stored
            source = "the most recent scheduled early-warning run"
        else:
            computed = self._engine.early_warning(
                request.scope, as_of=request.today, district_ids=request.slots.district_ids or None
            )
            alerts = [
                {
                    "alert_id": a.alert_id, "scope_name": a.scope_name, "crime_sub_head": a.crime_sub_head,
                    "observed_count": a.observed_count, "baseline_mean": a.baseline_mean,
                    "z_score": a.z_score, "severity": a.severity, "case_ids": a.case_ids,
                    "window_start": a.window_start, "window_end": a.window_end, "explanation": a.explanation,
                }
                for a in computed
            ]
            source = "an on-demand computation"

        organised = self._organised_activity(request)
        if organised:
            # Surfaced alongside the statistical alerts because both answer the
            # same operational question — "what should we look at" — while
            # measuring different things: one a rate departure, one a group
            # that keeps reappearing together.
            alerts = list(alerts) + organised
            source = f"{source}, plus recurring co-accused clusters in the link graph"

        if not alerts:
            nothing = empty_result_evidence(
                key="early_warning:none",
                label="No district × crime sub-head exceeded its own baseline",
                detail={"sigma_threshold": 2.0, "baseline_months": 12, "window_days": 30},
            )
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(
                    "No district and crime-type combination is currently running above its own 12-month baseline "
                    "by the configured threshold.",
                    [nothing], provenance=Provenance.DETERMINISTIC_COMPUTATION,
                )],
                evidence=[nothing],
                traces=[trace("early_warning",
                              "Compared each district × crime sub-head trailing-30-day count against its own "
                              "12-month monthly baseline using a z-score.",
                              inputs={"sigma_threshold": 2.0}, row_count=0)],
                confidence=0.85,
            )

        evidence_items = []
        claims = [claim(
            f"{len(alerts)} early-warning signal(s) are active, from {source}.",
            provenance=Provenance.DETERMINISTIC_COMPUTATION,
        )]
        for alert in alerts[:5]:
            item = alert_evidence(
                alert_id=str(alert["alert_id"]),
                label=f"{alert['scope_name']} · {alert.get('crime_sub_head') or 'all types'}",
                case_master_ids=[int(c) for c in alert.get("case_ids", [])],
                detail=alert.get("explanation", {}),
            )
            evidence_items.append(item)
            claims.append(claim(
                f"{alert['scope_name']}: {alert['observed_count']} {alert.get('crime_sub_head') or ''} case(s) "
                f"in the window ending {alert['window_end']}, against a monthly baseline of "
                f"{float(alert['baseline_mean']):.1f} — z = {float(alert['z_score']):.2f} ({alert['severity']}).",
                [item], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        claims[0].evidence_locators = [evidence_items[0].locator]
        claims.append(claim(
            "A z-score flags a statistical departure from a unit's own recent history. It is a prompt to look, "
            "not a conclusion that crime has risen."
        ))

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=evidence_items,
            traces=[trace(
                "early_warning",
                "For each district × crime sub-head, counted cases in the trailing window and compared them to "
                "the mean and standard deviation of that combination's own preceding monthly counts.",
                inputs={"alerts": len(alerts)},
                formula="z = (observed − mean(baseline)) / max(stddev(baseline), 1.0)",
                row_count=len(alerts),
            )],
            payload=StructuredPayload(
                payload_type="early_warning",
                title="Active early-warning signals",
                # Keys here are read directly by ``EarlyWarningAlerts`` in
                # PayloadView.jsx. A rename on either side blanks the panel, so
                # test_payload_contract.py pins the two together.
                data={
                    "alerts": [
                        {
                            "alert_id": str(a["alert_id"]),
                            "severity": str(a["severity"]).lower(),
                            "crime_sub_head": a.get("crime_sub_head") or None,
                            "district_name": str(a["scope_name"]),
                            "unit_name": None,
                            "period": f"{a['window_start']} → {a['window_end']}",
                            "description": (
                                f"{a['observed_count']} case(s) against a baseline of "
                                f"{float(a['baseline_mean']):.1f} per month."
                            ),
                            "sigma": round(float(a["z_score"]), 3),
                            "observed_count": int(a["observed_count"]),
                            "baseline_mean": round(float(a["baseline_mean"]), 2),
                        }
                        for a in alerts
                    ],
                    "caveat": (
                        "A z-score flags a statistical departure from a unit's own recent history. "
                        "It is a prompt to look, not a conclusion that crime has risen, and it is "
                        "not a prediction of future offences."
                    ),
                },
            ),
            data={"case_master_ids": [int(c) for a in alerts for c in a.get("case_ids", [])][:200],
                  "alert_ids": [a["alert_id"] for a in alerts]},
        )

    # ---------------------------------------------------------- sociology
    def _sociology(self, request: AgentRequest) -> AgentResult:
        subject = str(request.options.get("subject") or self._infer_subject(request.text_english))
        dimension = str(request.options.get("dimension") or self._infer_dimension(request.text_english))
        self._authorization.assert_aggregate_only_dimension(request.principal, dimension)

        substitution_note = None
        if subject == "victim" and dimension not in VICTIM_DIMENSIONS:
            substitution_note = (
                f"The victim record carries only age and gender, not {dimension.replace('_', ' ')} — showing "
                "gender instead."
            )
            dimension = "gender"

        start, end = default_date_window(request.slots, today=request.today, months=24)
        filters = AggregateFilter(
            unit_ids=request.slots.unit_ids or None,
            district_ids=request.slots.district_ids or None,
            crime_sub_head_ids=request.slots.crime_sub_head_ids or None,
            date_from=start, date_to=end,
        )
        result = self._engine.sociology(filters, request.scope, dimension=dimension, subject=subject)
        if result.total_records == 0:
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(f"No {subject} records fall inside that filter.")],
                traces=[result.trace], confidence=0.85,
            )

        evidence = aggregate_evidence(
            key=f"sociology:{subject}:{dimension}:{start}:{end}",
            label=f"{result.total_records} {subject} records grouped by {dimension}",
            case_master_ids=result.case_ids,
            detail={"dimension": dimension, "subject": subject, "window": [str(start), str(end)],
                    "suppression_threshold": result.suppression_threshold},
        )
        claims = []
        if substitution_note:
            claims.append(claim(substitution_note))
        claims.append(claim(
            f"{result.total_records} {subject} record(s) between {start} and {end} break down by "
            f"{dimension.replace('_', ' ')} as follows.",
            [evidence], provenance=Provenance.DETERMINISTIC_COMPUTATION,
        ))
        shown = list(result.top_associations[:5])
        suppressed_entry = next((row for row in result.top_associations if row.get("suppressed")), None)
        if suppressed_entry and suppressed_entry not in shown:
            shown.append(suppressed_entry)
        for row in shown:
            if row.get("suppressed"):
                claims.append(claim(
                    f"{row['value']}: {row['records']} record(s) combined, {row['share_percent']:.1f}% of the "
                    "total — shown as a merged group rather than individually so a small group cannot be "
                    "re-identified.",
                    [evidence], provenance=Provenance.DETERMINISTIC_COMPUTATION,
                ))
                continue
            top_type = row["top_crime_sub_heads"][0]["sub_head"] if row["top_crime_sub_heads"] else "unclassified"
            claims.append(claim(
                f"{row['value']}: {row['records']} record(s), {row['share_percent']:.1f}% of the total; "
                f"most common associated offence type is {top_type}.",
                [evidence], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        claims.append(claim(
            f"These are counts of recorded {subject}s, not rates against population. Differences in reporting "
            "propensity, policing intensity and base rates all affect them, so association here is not causation "
            "and must not be read as a statement about any community."
        ))

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=[evidence],
            traces=[result.trace],
            payload=StructuredPayload(
                payload_type="bar",
                title=f"{subject.capitalize()} records by {dimension.replace('_', ' ')}",
                data={
                    "labels": [row["value"] for row in result.top_associations],
                    "series": [{"name": "Records", "values": [row["records"] for row in result.top_associations]}],
                },
            ),
            data={"dimension": dimension, "subject": subject, "case_master_ids": result.case_ids[:200]},
        )

    @staticmethod
    def _infer_subject(text: str) -> str:
        lowered = text.casefold()
        if "victim" in lowered:
            return "victim"
        return "complainant"

    @staticmethod
    def _infer_dimension(text: str) -> str:
        lowered = text.casefold()
        for dimension in SOCIOLOGY_DIMENSIONS:
            if dimension.replace("_", " ") in lowered:
                return dimension
        if "age" in lowered:
            return "age_band"
        if "job" in lowered or "work" in lowered or "profession" in lowered:
            return "occupation"
        if "gender" in lowered or "women" in lowered or "men" in lowered:
            return "gender"
        return "occupation"

    def _scope_label(self, request: AgentRequest) -> str:
        if request.slots.district_names:
            return " and ".join(request.slots.district_names[:3])
        if request.slots.unit_names:
            return " and ".join(request.slots.unit_names[:3])
        return "across your authorized area"

    def _socioeconomic_correlation(self, request: AgentRequest) -> AgentResult:
        if self._correlator is None:
            from ..analytics.socioeconomic import SocioEconomicCorrelator
            from ...infrastructure.db.repositories.socioeconomic import SocioEconomicRepository

            self._correlator = SocioEconomicCorrelator(
                self._analytics, SocioEconomicRepository(self._analytics._store)
            )

        start, end = default_date_window(request.slots, today=request.today, months=24)
        result = self._correlator.correlate(
            request.scope,
            census_year=2011,
            crime_sub_head_ids=request.slots.crime_sub_head_ids or None,
            date_from=start,
            date_to=end,
        )

        if not result.correlations:
            nothing = empty_result_evidence(
                key=f"socioeconomic:insufficient:{result.district_count}",
                label=f"Only {result.district_count} district(s) available for correlation — minimum 5 required",
                detail={"district_count": result.district_count, "data_source": result.data_source},
            )
            return AgentResult(
                agent=self.name,
                intent=request.intent,
                summary_claims=[
                    claim(
                        f"Insufficient district data for socio-economic correlation: only {result.district_count} "
                        "district(s) have data in the requested window. A minimum of 5 districts is required "
                        "for correlation analysis.",
                        [nothing],
                        provenance=Provenance.DETERMINISTIC_COMPUTATION,
                    ),
                ],
                evidence=[nothing],
                traces=[result.trace],
                confidence=0.85,
                warnings=["Insufficient district count for correlation analysis."],
            )

        evidence = aggregate_evidence(
            key=f"socioeconomic:correlation:{result.census_year}:{len(result.correlations)}",
            label=f"Socio-economic correlation across {result.district_count} districts ({result.data_source})",
            detail={
                "district_count": result.district_count,
                "census_year": result.census_year,
                "data_source": result.data_source,
                "data_quality": result.data_quality,
            },
        )

        claims = [
            claim(
                f"Socio-economic correlation analysis across {result.district_count} Karnataka district(s) "
                f"using district-level indicators ({result.data_source}). Data quality is explicitly marked "
                f"'{result.data_quality}'.",
                [evidence],
                provenance=Provenance.SYNTHETIC_EXTENSION
                if result.data_quality == "synthetic"
                else Provenance.DETERMINISTIC_COMPUTATION,
            ),
        ]

        for c in result.correlations[:4]:
            claims.append(
                claim(
                    f"{c.label}: Pearson r = {c.pearson_r:+.2f} across {c.district_count} districts. {c.interpretation}",
                    [evidence],
                    provenance=Provenance.DETERMINISTIC_COMPUTATION,
                )
            )

        claims.append(
            claim(
                "These correlations reflect cross-district statistical association, not individual causality. "
                "Differences in reporting propensity, policing intensity, and urbanization base rates affect crime rates."
            )
        )

        payload_data = {
            "census_year": result.census_year,
            "data_source": result.data_source,
            "data_quality": result.data_quality,
            "district_count": result.district_count,
            "correlations": [
                {
                    "indicator": c.indicator,
                    "label": c.label,
                    "pearson_r": c.pearson_r,
                    "district_count": c.district_count,
                    "interpretation": c.interpretation,
                }
                for c in result.correlations
            ],
            "district_profiles": [
                {
                    "district_id": p.district_id,
                    "district_name": p.district_name,
                    "case_count": p.case_count,
                    "population": p.population,
                    "crime_rate_per_100k": p.crime_rate_per_100k,
                    "indicators": p.indicators,
                }
                for p in result.district_profiles[:15]
            ],
        }

        return AgentResult(
            agent=self.name,
            intent=request.intent,
            summary_claims=claims,
            evidence=[evidence],
            traces=[result.trace],
            payload=StructuredPayload(
                payload_type="socioeconomic_correlation",
                title=f"Socio-Economic Correlation ({result.district_count} Districts)",
                data=payload_data,
            ),
            data=payload_data,
        )

