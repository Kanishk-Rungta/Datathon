"""Every seasonal answer disclaims forecasting — including the empty ones.

The disclaimer used to live only on the branch that had something to report.
On a short history (or simply on the wrong day of the month, since the seed
window is anchored to "today") every calendar bucket is
``insufficient_history``, the agent takes an early return, and the answer went
out saying "no seasonal finding is reported" with nothing to stop a reader
concluding the platform had *predicted* a quiet month.

``_seasonal`` needs nothing from the agent but its analytics engine, so the
three branches are exercised directly against a stub rather than through a
seeded database — which is also the only way to reach the empty branches
deterministically.
"""

from __future__ import annotations

from datetime import date

import pytest

from ksp_cip.application.agents.base import AgentRequest
from ksp_cip.application.agents.crime_analytics import (
    SEASONAL_DISCLAIMER,
    CrimeAnalyticsAgent,
)
from ksp_cip.application.analytics.engine import SeasonalBucket, SeasonalityResult
from ksp_cip.domain.enums import Intent
from ksp_cip.domain.models import ComputationTrace, Slots, UnitScope

TRACE = ComputationTrace(operation="seasonality", description="stub", row_count=0)


class StubEngine:
    def __init__(self, result: SeasonalityResult) -> None:
        self._result = result

    def seasonality(self, filters, scope, **kwargs) -> SeasonalityResult:
        return self._result


def bucket(month: str, *, insufficient: bool) -> SeasonalBucket:
    return SeasonalBucket(
        key=month,
        label="August",
        current_period=f"2026-{month}",
        current_count=12,
        baseline_years=[] if insufficient else [2025, 2024],
        baseline_mean=0.0 if insufficient else 6.0,
        baseline_stddev=0.0 if insufficient else 1.0,
        deviation_percent=None if insufficient else 100.0,
        z_score=None if insufficient else 2.5,
        insufficient_history=insufficient,
        case_ids=[] if insufficient else [1, 2, 3],
    )


def agent(result: SeasonalityResult) -> CrimeAnalyticsAgent:
    # _seasonal reads only the engine and the request's own slots.
    return CrimeAnalyticsAgent(
        audit=None, engine=StubEngine(result), analytics=None, reference=None,
        hotspots=None, alerts=None, authorization=None,
    )


def request() -> AgentRequest:
    return AgentRequest(
        principal=None, intent=Intent.SEASONAL_QUERY, slots=Slots(),
        scope=UnitScope(statewide=True), text_english="Is there a seasonal pattern?",
        session_id="s", today=date(2026, 8, 29),
    )


def result_with(buckets: list[SeasonalBucket]) -> SeasonalityResult:
    return SeasonalityResult(
        grouping="month", comparison_years=3, buckets=buckets,
        total_periods_considered=len(buckets), trace=TRACE,
    )


@pytest.mark.parametrize("buckets,branch", [
    ([], "no cases matched at all"),
    ([bucket("08", insufficient=True)], "every bucket lacks prior years"),
    ([bucket("08", insufficient=False)], "a bucket is reportable"),
])
def test_the_disclaimer_is_on_every_branch(buckets, branch):
    answer = agent(result_with(buckets)).\
        _seasonal(request())  # noqa: SLF001 - the branch under test
    text = " ".join(claim.text for claim in answer.summary_claims)
    assert "not a forecast" in text.lower(), f"missing on the branch where {branch}"
    assert SEASONAL_DISCLAIMER in text


@pytest.mark.parametrize("buckets", [
    [],
    [bucket("08", insufficient=True)],
    [bucket("08", insufficient=False)],
])
def test_no_numeric_claim_goes_out_unevidenced(buckets):
    """The composer would reject these; assert it here so the failure names
    the branch rather than surfacing as an EvidenceMissingError elsewhere."""
    answer = agent(result_with(buckets))._seasonal(request())  # noqa: SLF001
    published = {item.locator for item in answer.evidence}
    for claim in answer.summary_claims:
        if any(character.isdigit() for character in claim.text):
            assert claim.evidence_locators, f"unevidenced numeric claim: {claim.text}"
            assert set(claim.evidence_locators) <= published
