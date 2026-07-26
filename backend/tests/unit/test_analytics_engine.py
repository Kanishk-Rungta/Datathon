"""Engine-level tests for seasonality and sociology guardrails.

These use a fake repository rather than the seeded SQLite database, because
the seeded fixture only spans 24 months (``conftest.SEED_MONTHS``) — not
enough distinct years to exercise the "sufficient history" branch of
seasonality. Pure fixtures let the arithmetic itself be checked exactly.
"""

from __future__ import annotations

from typing import Any

import pytest

from ksp_cip.application.analytics.engine import AnalyticsEngine
from ksp_cip.domain.models import UnitScope
from ksp_cip.infrastructure.db.repositories import AggregateFilter


class FakeAnalyticsRepository:
    def __init__(self, *, monthly_counts=None, demographics=None, window_counts=None) -> None:
        self._monthly_counts = monthly_counts or []
        self._demographics = demographics or []
        #: (date_from, date_to) -> count, for event-window comparison.
        self._window_counts = window_counts or {}

    def monthly_counts(self, filters, scope):
        return self._monthly_counts

    def case_ids_for(self, filters, scope, limit=200):
        return [{"case_master_id": 1, "crime_no": "x"}]

    def complainant_demographics(self, filters, scope, *, dimension):
        return self._demographics

    def victim_demographic_dimension(self, filters, scope, *, dimension):
        return self._demographics

    def counts_between(self, filters, scope, *, date_from, date_to):
        return self._window_counts.get((date_from.isoformat(), date_to.isoformat()), 0)


@pytest.fixture
def scope() -> UnitScope:
    return UnitScope(statewide=True)


@pytest.fixture
def filters() -> AggregateFilter:
    return AggregateFilter()


class TestSeasonality:
    def test_a_month_with_three_prior_years_gets_a_z_score(self, scope, filters):
        repo = FakeAnalyticsRepository(monthly_counts=[
            {"period": "2023-01", "case_count": 10},
            {"period": "2024-01", "case_count": 12},
            {"period": "2025-01", "case_count": 11},
            {"period": "2026-01", "case_count": 30},
        ])
        engine = AnalyticsEngine(repo, reference=object())
        result = engine.seasonality(filters, scope)

        assert result.total_periods_considered == 4
        bucket = result.buckets[0]
        assert bucket.label == "January"
        assert bucket.current_period == "2026-01"
        assert bucket.current_count == 30
        assert bucket.insufficient_history is False
        assert bucket.baseline_years == [2025, 2024, 2023]
        assert bucket.baseline_mean == 11.0
        assert bucket.z_score == pytest.approx(19.0)
        assert bucket.deviation_percent == pytest.approx(172.73, abs=0.01)

    def test_a_month_with_only_one_prior_year_is_insufficient(self, scope, filters):
        repo = FakeAnalyticsRepository(monthly_counts=[
            {"period": "2025-02", "case_count": 5},
            {"period": "2026-02", "case_count": 7},
        ])
        engine = AnalyticsEngine(repo, reference=object())
        result = engine.seasonality(filters, scope)

        bucket = result.buckets[0]
        assert bucket.insufficient_history is True
        assert bucket.z_score is None
        assert bucket.deviation_percent is None

    def test_no_data_returns_an_empty_result_not_an_error(self, scope, filters):
        engine = AnalyticsEngine(FakeAnalyticsRepository(), reference=object())
        result = engine.seasonality(filters, scope)
        assert result.buckets == []
        assert result.trace.row_count == 0


class TestEventComparison:
    """A 7-day event window against four matched preceding 7-day windows."""

    EVENT = {
        "event_id": "ev-1",
        "event_name": "Dasara",
        "event_type": "festival",
        "date_from": "2026-10-15",
        "date_to": "2026-10-21",
    }

    def _repo(self, observed: int, priors: list[int]) -> FakeAnalyticsRepository:
        windows = {("2026-10-15", "2026-10-21"): observed}
        # Four contiguous 7-day windows immediately before the event.
        bounds = [
            ("2026-10-08", "2026-10-14"),
            ("2026-10-01", "2026-10-07"),
            ("2026-09-24", "2026-09-30"),
            ("2026-09-17", "2026-09-23"),
        ]
        for (start, end), count in zip(bounds, priors):
            windows[(start, end)] = count
        return FakeAnalyticsRepository(window_counts=windows)

    def test_matched_windows_are_contiguous_and_equal_length(self, scope, filters):
        engine = AnalyticsEngine(self._repo(30, [10, 12, 11, 9]), reference=object())
        result = engine.event_comparison(filters, scope, event=self.EVENT)

        assert result.window_days == 7
        assert result.sample_size == 4
        assert [w["count"] for w in result.comparison_windows] == [10, 12, 11, 9]
        assert result.comparison_windows[0]["end"] == "2026-10-14"

    def test_an_elevated_window_is_quantified(self, scope, filters):
        engine = AnalyticsEngine(self._repo(30, [10, 12, 11, 9]), reference=object())
        result = engine.event_comparison(filters, scope, event=self.EVENT)

        assert result.observed_count == 30
        assert result.comparison_mean == 10.5
        assert result.sufficient_evidence is True
        assert result.difference_percent == pytest.approx(185.71, abs=0.01)
        assert result.z_score is not None and result.z_score > 0

    def test_empty_comparison_windows_yield_no_finding(self, scope, filters):
        """Zero prior cases makes a percentage difference arithmetic theatre."""
        engine = AnalyticsEngine(self._repo(5, [0, 0, 0, 0]), reference=object())
        result = engine.event_comparison(filters, scope, event=self.EVENT)

        assert result.sufficient_evidence is False
        assert result.difference_percent is None
        assert result.z_score is None

    def test_the_trace_refuses_causal_language(self, scope, filters):
        engine = AnalyticsEngine(self._repo(30, [10, 12, 11, 9]), reference=object())
        result = engine.event_comparison(filters, scope, event=self.EVENT)

        description = result.trace.description.lower()
        assert "not whether the event caused" in description
        assert "caused by" not in description

    def test_the_result_carries_no_causal_field(self, scope, filters):
        """Nothing in the payload can be read as a cause claim."""
        engine = AnalyticsEngine(self._repo(30, [10, 12, 11, 9]), reference=object())
        result = engine.event_comparison(filters, scope, event=self.EVENT)

        fields = set(result.__slots__)
        assert not {"cause", "caused_by", "causal", "attribution"} & fields


class TestSociologySuppression:
    def test_small_groups_are_merged_not_shown_individually(self, scope, filters):
        rows = [
            {"dimension_value": "Farmer", "sub_head": "Theft", "record_count": 40},
            {"dimension_value": "Driver", "sub_head": "Theft", "record_count": 3},
            {"dimension_value": "Tailor", "sub_head": "Assault", "record_count": 2},
        ]
        repo = FakeAnalyticsRepository(demographics=rows)
        engine = AnalyticsEngine(repo, reference=object())
        result = engine.sociology(filters, scope, dimension="occupation", suppression_threshold=10)

        values = {row["value"]: row for row in result.top_associations}
        assert "Farmer" in values
        assert "Driver" not in values
        assert "Tailor" not in values
        assert result.suppressed_group_count == 2
        assert result.suppressed_record_count == 5
        suppressed_rows = [row for row in result.top_associations if row.get("suppressed")]
        assert len(suppressed_rows) == 1
        assert suppressed_rows[0]["records"] == 5

    def test_no_suppression_when_every_group_clears_the_threshold(self, scope, filters):
        rows = [
            {"dimension_value": "Farmer", "sub_head": "Theft", "record_count": 40},
            {"dimension_value": "Driver", "sub_head": "Theft", "record_count": 15},
        ]
        repo = FakeAnalyticsRepository(demographics=rows)
        engine = AnalyticsEngine(repo, reference=object())
        result = engine.sociology(filters, scope, dimension="occupation", suppression_threshold=10)

        assert result.suppressed_group_count == 0
        assert not any(row.get("suppressed") for row in result.top_associations)

    def test_subject_dispatches_to_the_victim_repository_method(self, scope, filters):
        rows = [{"dimension_value": "M", "sub_head": "Theft", "record_count": 20}]
        repo = FakeAnalyticsRepository(demographics=rows)
        engine = AnalyticsEngine(repo, reference=object())
        result = engine.sociology(filters, scope, dimension="gender", subject="victim")
        assert result.subject == "victim"
        assert result.total_records == 20
