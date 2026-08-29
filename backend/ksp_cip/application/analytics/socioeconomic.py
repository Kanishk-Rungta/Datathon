"""Socio-economic correlation analytics.

Correlates district-level crime rates (from the FIR database) with district-level
socio-economic indicators (from ext_socioeconomic_indicator) to surface which
social factors are statistically associated with higher or lower crime rates across
Karnataka's 31 districts.

Design principles:
- All arithmetic is here, not in an LLM. The LLM can only phrase results.
- Crime rate = cases per 100,000 population (to control for district size).
- Correlation metric = Pearson r over the 31-district cross-section.
- Requires data for at least MIN_DISTRICTS districts to publish a finding.
- Every result carries an explicit trace and a mandatory caveat that correlation
  is not causation and that crime-rate figures reflect reporting and policing
  intensity, not underlying crime.
- Sensitive dimensions (caste, religion) are intentionally excluded from
  correlation output; those appear only in the sociology crosstab with
  small-cell suppression.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ...domain.models import ComputationTrace
from ...infrastructure.db.repositories import AggregateFilter, AnalyticsRepository
from ...infrastructure.db.repositories.socioeconomic import SocioEconomicRepository

#: Minimum number of districts with complete data (both crime counts and
#: indicators) before a correlation is published. Below this, Pearson r has
#: essentially no statistical power and the figure would be misleading.
MIN_DISTRICTS = 5

#: Indicators exposed in the correlation output. Sensitive dimensions
#: (caste, religion) are intentionally absent here — they are available
#: only in the sociology crosstab with small-cell suppression.
INDICATOR_LABELS: dict[str, str] = {
    "literacy_rate": "Overall literacy rate (%)",
    "female_literacy": "Female literacy rate (%)",
    "urbanization_percent": "Urban population share (%)",
    "unemployment_rate": "Unemployment rate (%)",
    "poverty_headcount": "Poverty headcount ratio (%)",
    "migration_inflow_rate": "Migrant population share (%)",
    "per_capita_income_index": "Per-capita income (relative index)",
    "population_density": "Population density (persons/sq km)",
}


@dataclass(slots=True)
class IndicatorCorrelation:
    """Pearson r of one socio-economic indicator vs crime rate per 100k population."""

    indicator: str
    label: str
    pearson_r: float
    district_count: int
    interpretation: str


@dataclass(slots=True)
class DistrictCrimeProfile:
    """Crime rate and indicator values for one district — used to build scatter data."""

    district_id: int
    district_name: str
    case_count: int
    population: int
    crime_rate_per_100k: float
    indicators: dict[str, float | None]


@dataclass(slots=True)
class SocioEconomicResult:
    """Full correlation result.

    There is deliberately no field here that names an individual or that could
    be read as a statement about a community's propensity to offend. This object
    describes relationships between *aggregate statistics*, and the type system
    is the cheapest enforcement mechanism.
    """

    census_year: int
    data_source: str
    data_quality: str
    district_count: int
    correlations: list[IndicatorCorrelation]
    district_profiles: list[DistrictCrimeProfile]
    trace: ComputationTrace


class SocioEconomicCorrelator:
    """Compute correlations between district crime rates and socio-economic indicators.

    Injected with the analytics and socio-economic repositories so the pure
    arithmetic in this class is tested independently of the database adapters.
    """

    def __init__(
        self,
        analytics: AnalyticsRepository,
        socioeconomic: SocioEconomicRepository,
    ) -> None:
        self._analytics = analytics
        self._socioeconomic = socioeconomic

    def correlate(
        self,
        scope: Any,  # UnitScope — kept Any to avoid a circular import
        *,
        census_year: int = 2011,
        crime_sub_head_ids: list[int] | None = None,
        date_from: Any | None = None,
        date_to: Any | None = None,
    ) -> SocioEconomicResult:
        """Compute Pearson r between each socio-economic indicator and crime rate.

        Args:
            scope: The caller's UnitScope (used to authorise the crime-count query).
            census_year: Which indicator vintage to compare against. Defaults to 2011.
            crime_sub_head_ids: If set, restricts the crime rate to specific sub-heads.
            date_from / date_to: Optional date window for the crime-count side.

        Returns:
            A SocioEconomicResult carrying correlations and district profiles,
            with an empty correlations list if fewer than MIN_DISTRICTS have data.
        """
        # Fetch district-level crime counts (aggregate query, no individual data).
        filters = AggregateFilter(
            crime_sub_head_ids=crime_sub_head_ids,
            date_from=date_from,
            date_to=date_to,
        )
        crime_rows = self._analytics.counts_by_district(filters, scope)
        crime_by_district: dict[int, int] = {
            int(row["district_id"]): int(row["case_count"])
            for row in crime_rows
            if row.get("district_id")
        }

        # Fetch socio-economic indicators.
        indicator_by_district = self._socioeconomic.indicators_by_district(census_year=census_year)

        # Build district profiles for districts where both sides have data.
        profiles: list[DistrictCrimeProfile] = []
        for district_id, ind in indicator_by_district.items():
            population = ind.get("population") or 0
            if population <= 0:
                continue  # Cannot compute a rate without a denominator.
            case_count = crime_by_district.get(district_id, 0)
            crime_rate = (case_count / population) * 100_000

            profile = DistrictCrimeProfile(
                district_id=district_id,
                district_name=str(ind.get("district_name") or ""),
                case_count=case_count,
                population=population,
                crime_rate_per_100k=round(crime_rate, 2),
                indicators={
                    key: _safe_float(ind.get(key))
                    for key in INDICATOR_LABELS
                },
            )
            profiles.append(profile)

        profiles.sort(key=lambda p: p.crime_rate_per_100k, reverse=True)

        # Derive data-source label from the first indicator row (all have the same).
        first_ind = next(iter(indicator_by_district.values()), {})
        data_source = str(first_ind.get("data_source") or "synthetic")
        data_quality = str(first_ind.get("data_quality") or "synthetic")

        if len(profiles) < MIN_DISTRICTS:
            # Not enough districts: return an honest "no finding" rather than a
            # low-power correlation that looks authoritative but means nothing.
            trace = ComputationTrace(
                operation="socioeconomic_correlation",
                description=(
                    f"Only {len(profiles)} district(s) had complete crime-count and "
                    f"indicator data for census_year={census_year}. A Pearson correlation "
                    f"requires at least {MIN_DISTRICTS} observations to be meaningful."
                ),
                inputs={"census_year": census_year, "districts_with_data": len(profiles)},
                row_count=0,
            )
            return SocioEconomicResult(
                census_year=census_year,
                data_source=data_source,
                data_quality=data_quality,
                district_count=len(profiles),
                correlations=[],
                district_profiles=profiles,
                trace=trace,
            )

        # Compute Pearson r for each indicator.
        correlations: list[IndicatorCorrelation] = []
        crime_rates = [p.crime_rate_per_100k for p in profiles]

        for indicator_key, indicator_label in INDICATOR_LABELS.items():
            indicator_values = [p.indicators.get(indicator_key) for p in profiles]
            # Only use districts where the indicator value is known.
            pairs = [
                (c, v)
                for c, v in zip(crime_rates, indicator_values)
                if v is not None
            ]
            if len(pairs) < MIN_DISTRICTS:
                continue
            xs = [c for c, _ in pairs]
            ys = [v for _, v in pairs]
            r = _pearson_r(xs, ys)
            if r is None:
                continue
            correlations.append(IndicatorCorrelation(
                indicator=indicator_key,
                label=indicator_label,
                pearson_r=round(r, 4),
                district_count=len(pairs),
                interpretation=_interpret_r(r, indicator_key),
            ))

        # Sort strongest absolute correlation first.
        correlations.sort(key=lambda c: abs(c.pearson_r), reverse=True)

        trace = ComputationTrace(
            operation="socioeconomic_correlation",
            description=(
                f"Computed Pearson r between district-level crime rate per 100,000 population "
                f"and each of {len(INDICATOR_LABELS)} socio-economic indicators across "
                f"{len(profiles)} Karnataka district(s). Crime counts are from registered FIRs; "
                f"indicators are from {data_source}. "
                "This is an association measure over a 31-district cross-section, not a causal "
                "model. Policing intensity, reporting propensity and data collection differences "
                "all affect the crime-rate figure."
            ),
            inputs={
                "census_year": census_year,
                "district_count": len(profiles),
                "indicators_tested": list(INDICATOR_LABELS.keys()),
                "data_source": data_source,
                "data_quality": data_quality,
            },
            row_count=len(profiles),
            formula="Pearson r = Σ(xi−x̄)(yi−ȳ) / √[Σ(xi−x̄)² · Σ(yi−ȳ)²]",
            components=[
                {
                    "indicator": c.indicator,
                    "pearson_r": c.pearson_r,
                    "district_count": c.district_count,
                }
                for c in correlations
            ],
        )
        return SocioEconomicResult(
            census_year=census_year,
            data_source=data_source,
            data_quality=data_quality,
            district_count=len(profiles),
            correlations=correlations,
            district_profiles=profiles,
            trace=trace,
        )


# ------------------------------------------------------------------ helpers


def _safe_float(value: Any) -> float | None:
    """Return a float or None — never raises."""
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _pearson_r(xs: list[float], ys: list[float]) -> float | None:
    """Sample Pearson correlation coefficient. Returns None when undefined."""
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denom_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    denom_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    denom = denom_x * denom_y
    if denom == 0.0:
        return None
    return numerator / denom


def _interpret_r(r: float, indicator: str) -> str:
    """Return a plain-English interpretation of the direction and strength of r.

    The interpretation never says "causes" — it always says "associated with"
    to avoid a causal overclaim.
    """
    abs_r = abs(r)
    direction = "positively" if r > 0 else "negatively"
    if abs_r >= 0.7:
        strength = "strongly"
    elif abs_r >= 0.4:
        strength = "moderately"
    elif abs_r >= 0.2:
        strength = "weakly"
    else:
        strength = "negligibly"
    return (
        f"Districts with higher {indicator.replace('_', ' ')} tend to have "
        f"{direction} {strength} associated crime rates (r = {r:.2f}). "
        "This is a cross-district association, not a causal relationship."
    )
