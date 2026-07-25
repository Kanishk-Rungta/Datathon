"""Statistical primitives.

Small, pure, and unit-tested. Every analytic figure the platform reports is
produced by one of these functions, so the arithmetic behind an answer can be
pointed at, argued with, and re-run by hand.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Sequence


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def population_stddev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mu = mean(values)
    return math.sqrt(sum((v - mu) ** 2 for v in values) / len(values))


def sample_stddev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mu = mean(values)
    return math.sqrt(sum((v - mu) ** 2 for v in values) / (len(values) - 1))


def z_score(observed: float, baseline: Sequence[float], *, floor: float = 1.0) -> float:
    """Standard score against a baseline window.

    ``floor`` prevents a zero-variance baseline from producing an infinite
    score: with a floor of 1.0 the score is expressed in "cases above the
    mean" when the baseline never varies, which is the honest reading.
    """
    if not baseline:
        return 0.0
    mu = mean(baseline)
    sigma = max(sample_stddev(baseline), floor)
    return (observed - mu) / sigma


def rolling_baseline(series: Sequence[float], window: int) -> list[float]:
    """Trailing mean excluding the current point (no look-ahead leakage)."""
    result: list[float] = []
    for index in range(len(series)):
        start = max(0, index - window)
        history = series[start:index]
        result.append(mean(history) if history else 0.0)
    return result


def linear_trend(values: Sequence[float]) -> tuple[float, float]:
    """Ordinary least squares slope and intercept over evenly spaced points."""
    n = len(values)
    if n < 2:
        return 0.0, values[0] if values else 0.0
    xs = list(range(n))
    x_mean = mean([float(x) for x in xs])
    y_mean = mean(values)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator == 0:
        return 0.0, y_mean
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values)) / denominator
    return slope, y_mean - slope * x_mean


def percent_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return (current - previous) / previous * 100.0


@dataclass(frozen=True, slots=True)
class Period:
    key: str
    start: date
    end: date


def month_periods(start: date, end: date) -> list[Period]:
    periods: list[Period] = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        if cursor.month == 12:
            nxt = date(cursor.year + 1, 1, 1)
        else:
            nxt = date(cursor.year, cursor.month + 1, 1)
        periods.append(Period(key=cursor.strftime("%Y-%m"), start=cursor, end=nxt - timedelta(days=1)))
        cursor = nxt
    return periods


def densify(counts: dict[str, int], periods: Iterable[Period]) -> list[tuple[str, int]]:
    """Fill missing periods with zero so charts and baselines are honest."""
    return [(period.key, int(counts.get(period.key, 0))) for period in periods]


def top_n(pairs: Sequence[tuple[str, float]], n: int) -> list[tuple[str, float]]:
    return sorted(pairs, key=lambda item: item[1], reverse=True)[:n]


def share(value: float, total: float) -> float:
    return (value / total * 100.0) if total else 0.0


def band_for(score: float, thresholds: Sequence[tuple[float, str]]) -> str:
    """Map a score to a named band. ``thresholds`` is descending by cut-off."""
    for cutoff, label in thresholds:
        if score >= cutoff:
            return label
    return thresholds[-1][1] if thresholds else "unknown"
