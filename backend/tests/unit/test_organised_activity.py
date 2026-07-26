"""Organised-activity signals over the co-accused graph (brief §8).

The hard part here is not finding groups — community detection does that
eagerly — it is *refusing* the ones that are not groups. Louvain will happily
return a 38-person connected region at 5% link density, and reporting that as
organised crime would be a confident answer the records cannot support.

So the gates are the feature, and they are what these tests hold in place.
"""

from __future__ import annotations

import networkx as nx
import pytest

from ksp_cip.application.graph.service import GraphService
from ksp_cip.domain.models import UnitScope

SCOPE = UnitScope(statewide=True)


class FakeGraphRepository:
    def __init__(self, edges):
        self._edges = edges

    def all_edges(self):
        return self._edges


def edge(source: str, target: str, case_ids, edge_type="CO_ACCUSED"):
    return {
        "edge_id": f"{source}-{target}-{edge_type}",
        "src_type": "person", "src_id": f"person:{source}",
        "dst_type": "person", "dst_id": f"person:{target}",
        "edge_type": edge_type, "weight": 1.0,
        "case_ids": list(case_ids), "unit_ids": [],
        "provenance": "inferred", "detail": {},
    }


def service(edges) -> GraphService:
    return GraphService(FakeGraphRepository(edges))


def clique(names, case_ids):
    """Every member linked to every other — a genuinely cohesive group."""
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            out.append(edge(a, b, case_ids))
    return out


def chain(names, case_ids):
    """A—B—C—D: connected, but nobody acts with more than their neighbours.

    Each hop gets its **own** case, which is what a chain looks like in real
    records — A and B on one FIR, B and C on another. Giving every hop the same
    case ids would make the whole chain look like one co-offending group, which
    is a property of the fixture rather than of the data.
    """
    return [
        edge(names[i], names[i + 1], [case_ids[i % len(case_ids)] + i * 100])
        for i in range(len(names) - 1)
    ]


class TestCohesionGate:
    def test_a_cohesive_recurring_group_is_reported(self):
        edges = clique(["A", "B", "C", "D"], [1, 2, 3])
        signals = service(edges).organised_activity(SCOPE)
        assert signals, "a fully-linked group sharing several cases must surface"
        assert signals[0].size == 4
        assert signals[0].cohesion == 1.0

    def test_a_loose_chain_is_not_reported_as_one_group(self):
        """The defect this gate exists for: connected is not the same as together.

        Community detection will still carve a long chain into small pieces —
        that is what it does. What must not happen is the 20-person component
        being handed to an officer as a single organised group.
        """
        edges = chain([f"P{i}" for i in range(20)], [1, 2, 3])
        signals = service(edges).organised_activity(SCOPE)
        assert all(s.size <= 4 for s in signals), (
            "a sprawling chain was reported as one cohesive group: "
            f"{[(s.size, s.cohesion) for s in signals]}"
        )
        # And nobody in a chain shares repeat cases, so recurrence excludes them.
        assert all(s.shared_case_count >= 2 for s in signals)

    def test_the_cohesion_gate_is_what_excludes_a_hub_and_spokes(self):
        """A star recurs but is not cohesive: the spokes never act with each other.

        This isolates the cohesion gate from the recurrence gate — the shared
        cases are present, so if the group is excluded it is density that did
        it. Dropping the gate surfaces the same group, proving the same.
        """
        edges = [edge("HUB", f"S{i}", [1, 2, 3]) for i in range(8)]
        graph = service(edges)
        assert graph.organised_activity(SCOPE) == []
        assert graph.organised_activity(SCOPE, min_cohesion=0.0, max_size=20)


class TestSizeGate:
    def test_a_sprawling_community_is_not_called_a_group(self):
        """Past a dozen people this is a region of the graph, not a gang."""
        big = clique([f"M{i}" for i in range(30)], [1, 2, 3])
        assert service(big).organised_activity(SCOPE) == []

    def test_a_group_below_the_minimum_size_is_ignored(self):
        assert service(clique(["A", "B"], [1, 2, 3])).organised_activity(SCOPE) == []


class TestRecurrenceGate:
    def test_one_shared_case_is_not_recurrence(self):
        """A single multi-accused FIR links everyone once; that is not a pattern."""
        edges = clique(["A", "B", "C", "D"], [1])
        assert service(edges).organised_activity(SCOPE) == []

    def test_repeated_appearances_together_qualify(self):
        edges = clique(["A", "B", "C", "D"], [1, 2, 3, 4])
        signals = service(edges).organised_activity(SCOPE)
        assert signals and signals[0].shared_case_count >= 2


class TestScoreAndEvidence:
    def test_the_score_is_bounded_and_banded(self):
        signals = service(clique(["A", "B", "C", "D"], [1, 2, 3, 4, 5, 6])).organised_activity(SCOPE)
        signal = signals[0]
        assert 0.0 <= signal.score <= 100.0
        assert signal.band in {"low", "medium", "high"}

    def test_cohesion_outweighs_breadth(self):
        """A tight group must outrank a sprawling one with the same case count."""
        tight = service(clique(["A", "B", "C", "D"], [1, 2, 3])).organised_activity(SCOPE)
        loose = service(
            clique(["A", "B", "C"], [1, 2, 3]) + chain(["C", "D", "E", "F", "G"], [1, 2, 3])
        ).organised_activity(SCOPE, min_cohesion=0.0)
        assert tight[0].score >= max((s.score for s in loose), default=0.0)

    def test_supporting_cases_are_carried_for_evidence(self):
        signal = service(clique(["A", "B", "C"], [11, 22, 33])).organised_activity(SCOPE)[0]
        assert set(signal.case_ids) >= {11, 22, 33}
        assert signal.member_labels and len(signal.member_labels) == signal.size

    def test_span_and_districts_are_measured_when_supplied(self):
        edges = clique(["A", "B", "C", "D"], [1, 2, 3])
        signal = service(edges).organised_activity(
            SCOPE,
            case_dates={1: "2024-01-01", 2: "2024-06-01", 3: "2025-01-01"},
            case_districts={1: 10, 2: 20, 3: 30},
        )[0]
        assert signal.district_count == 3
        assert signal.span_days > 300
        assert signal.first_seen == "2024-01-01" and signal.last_seen == "2025-01-01"


class TestLanguageStaysObservational:
    """A recurring association is not a finding that a gang exists."""

    def test_the_result_type_carries_no_verdict_field(self):
        from ksp_cip.application.graph.service import OrganisedActivitySignal

        forbidden = {"gang", "organised_crime", "verdict", "guilt", "conclusion", "culpability"}
        assert not forbidden & set(OrganisedActivitySignal.__slots__)

    def test_published_weights_are_reconstructible_from_the_signal(self):
        """A supervisor must be able to disagree with the arithmetic."""
        signal = service(clique(["A", "B", "C", "D"], [1, 2, 3])).organised_activity(SCOPE)[0]
        for field in ("cohesion", "shared_case_count", "district_count", "span_days", "score"):
            assert getattr(signal, field) is not None
