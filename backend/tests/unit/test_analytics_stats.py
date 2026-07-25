"""The arithmetic every published figure rests on."""

import math
from datetime import date

import pytest

from ksp_cip.application.analytics import stats


class TestDescriptives:
    def test_mean_and_population_stddev(self):
        values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        assert stats.mean(values) == 5.0
        assert stats.population_stddev(values) == pytest.approx(2.0)

    def test_sample_stddev_uses_bessel_correction(self):
        values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        assert stats.sample_stddev(values) > stats.population_stddev(values)

    def test_empty_inputs_do_not_raise(self):
        assert stats.mean([]) == 0.0
        assert stats.population_stddev([]) == 0.0
        assert stats.sample_stddev([]) == 0.0


class TestZScore:
    def test_known_value(self):
        # Baseline mean 10, sample stddev 5 -> an observation of 20 is +2 sigma.
        baseline = [5.0, 10.0, 15.0, 10.0]
        score = stats.z_score(20.0, baseline, floor=0.0001)
        assert score == pytest.approx((20 - 10) / stats.sample_stddev(baseline))

    def test_floor_prevents_division_blow_up(self):
        """A perfectly flat baseline must not yield an infinite score."""
        score = stats.z_score(5.0, [1.0, 1.0, 1.0, 1.0], floor=1.0)
        assert math.isfinite(score)
        assert score == pytest.approx(4.0)

    def test_empty_baseline_is_neutral(self):
        assert stats.z_score(99.0, []) == 0.0


class TestRollingBaseline:
    def test_baseline_never_includes_the_current_point(self):
        """No look-ahead: index i is computed from strictly earlier values."""
        series = [1.0, 2.0, 3.0, 4.0, 100.0]
        baselines = stats.rolling_baseline(series, window=4)
        assert baselines[0] == 0.0
        assert baselines[4] == pytest.approx(stats.mean([1.0, 2.0, 3.0, 4.0]))
        assert baselines[4] < 100.0, "the spike must not inflate its own baseline"

    def test_window_is_respected(self):
        """Only the trailing `window` points feed a baseline, not all history."""
        series = [10.0, 10.0, 10.0, 0.0, 0.0]
        wide = stats.rolling_baseline(series, window=4)
        narrow = stats.rolling_baseline(series, window=2)
        # index 4 sees [10, 10, 10, 0] wide, but only [10, 0] narrow.
        assert wide[4] == pytest.approx(7.5)
        assert narrow[4] == pytest.approx(5.0)


class TestTrend:
    def test_rising_series_has_positive_slope(self):
        slope, _intercept = stats.linear_trend([1, 2, 3, 4, 5])
        assert slope == pytest.approx(1.0)

    def test_flat_series_has_zero_slope(self):
        slope, intercept = stats.linear_trend([7, 7, 7, 7])
        assert slope == pytest.approx(0.0)
        assert intercept == pytest.approx(7.0)

    def test_percent_change(self):
        assert stats.percent_change(110, 100) == pytest.approx(10.0)
        assert stats.percent_change(10, 0) is None


class TestPeriods:
    def test_month_periods_are_contiguous_and_inclusive(self):
        periods = stats.month_periods(date(2026, 1, 15), date(2026, 4, 2))
        assert [p.key for p in periods] == ["2026-01", "2026-02", "2026-03", "2026-04"]
        assert periods[0].start == date(2026, 1, 1)
        assert periods[0].end == date(2026, 1, 31)

    def test_year_boundary_is_handled(self):
        periods = stats.month_periods(date(2025, 11, 1), date(2026, 2, 1))
        assert [p.key for p in periods] == ["2025-11", "2025-12", "2026-01", "2026-02"]

    def test_densify_fills_missing_months_with_zero(self):
        periods = stats.month_periods(date(2026, 1, 1), date(2026, 4, 1))
        filled = stats.densify({"2026-01": 5, "2026-04": 2}, periods)
        assert filled == [("2026-01", 5), ("2026-02", 0), ("2026-03", 0), ("2026-04", 2)]


class TestBands:
    def test_band_lookup_is_inclusive_at_the_boundary(self):
        bands = ((70.0, "high"), (45.0, "medium"), (0.0, "low"))
        assert stats.band_for(70.0, bands) == "high"
        assert stats.band_for(69.9, bands) == "medium"
        assert stats.band_for(0.0, bands) == "low"

    def test_share_is_a_percentage_and_safe_at_zero(self):
        assert stats.share(25, 100) == pytest.approx(25.0)
        assert stats.share(1, 0) == 0.0
