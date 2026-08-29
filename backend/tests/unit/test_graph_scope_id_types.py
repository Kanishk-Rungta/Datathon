"""Graph ACL trimming must survive string-typed unit ids.

The Catalyst Data Store returns every column as a string, so an edge built
while running on that backend carries unit_ids like ["2023"] while a scope
holds {2023}. `unit_id in scope.unit_ids` is then False for every edge and the
whole graph is trimmed as out-of-scope.

That direction is the safe one -- nothing leaks -- but a scoped officer would
see an empty link graph with no explanation, and the trim count would claim
their own station's edges were withheld from them.
"""

from __future__ import annotations

from ksp_cip.application.graph.service import GraphService
from ksp_cip.domain.models import UnitScope


def allowed(unit_ids, scope):
    return GraphService._edge_allowed(object.__new__(GraphService), {"unit_ids": unit_ids}, scope)


SCOPED = UnitScope(root_unit_id=2023, unit_ids=frozenset({2023, 2024}), statewide=False)


class TestEdgeVisibility:
    def test_int_ids_are_visible(self):
        assert allowed([2023], SCOPED) is True

    def test_string_ids_are_visible(self):
        """The Catalyst spelling of the same id."""
        assert allowed(["2023"], SCOPED) is True

    def test_out_of_scope_stays_hidden_as_a_string(self):
        assert allowed(["9999"], SCOPED) is False

    def test_out_of_scope_stays_hidden_as_an_int(self):
        assert allowed([9999], SCOPED) is False

    def test_one_in_scope_id_is_enough(self):
        assert allowed(["9999", "2024"], SCOPED) is True

    def test_a_non_numeric_id_does_not_raise(self):
        assert allowed(["not-a-number"], SCOPED) is False

    def test_statewide_sees_everything(self):
        assert allowed(["9999"], UnitScope(statewide=True)) is True

    def test_an_edge_without_unit_provenance_is_left_to_the_seed(self):
        assert allowed([], SCOPED) is True
