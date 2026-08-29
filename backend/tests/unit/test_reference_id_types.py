"""Ids must compare across the types two backends return.

SQLite hands back an INTEGER; the Catalyst Data Store returns every column as
a string. `u["UnitID"] == unit_id` is therefore True on one backend and False
on the other, which made unit()/district() return None on Catalyst only.

Consequences seen live: a scoped officer's console read "no unit assigned"
instead of their police station, and hotspot cells lost their district label.
Authorization was not affected -- the unit-subtree predicate comes from the
closure table -- but a screen about who may see what showed the wrong answer.
"""

from __future__ import annotations

import pytest

from ksp_cip.infrastructure.db.repositories.reference import ReferenceRepository, _same_id


class _Store:
    """Returns rows the way Catalyst does: every value a string."""

    def __init__(self, rows):
        self._rows = rows

    def query(self, sql, params=None):
        return list(self._rows)


class TestSameId:
    @pytest.mark.parametrize("left,right", [(2023, 2023), ("2023", 2023), (2023, "2023"), ("2023", "2023")])
    def test_equal_ids_match_across_types(self, left, right):
        assert _same_id(left, right) is True

    @pytest.mark.parametrize("left,right", [(2023, 2024), ("2023", "2024")])
    def test_different_ids_do_not_match(self, left, right):
        assert _same_id(left, right) is False

    def test_none_never_matches(self):
        assert _same_id(None, 1) is False
        assert _same_id(1, None) is False
        assert _same_id(None, None) is False

    def test_non_numeric_falls_back_to_string_comparison(self):
        assert _same_id("KA-01", "KA-01") is True
        assert _same_id("KA-01", "KA-02") is False


class TestLookupsSurviveStringIds:
    def _reference(self, rows):
        ref = ReferenceRepository(_Store(rows))
        return ref

    def test_unit_is_found_when_the_store_returns_strings(self, monkeypatch):
        ref = self._reference([{"UnitID": "2023", "UnitName": "Bengaluru City Market Police Station"}])
        monkeypatch.setattr(ref, "units", lambda: [{"UnitID": "2023", "UnitName": "Market PS"}])
        assert ref.unit(2023)["UnitName"] == "Market PS"

    def test_district_is_found_when_the_store_returns_strings(self, monkeypatch):
        ref = self._reference([])
        monkeypatch.setattr(ref, "districts", lambda: [{"DistrictID": "2904", "DistrictName": "Bengaluru City"}])
        assert ref.district(2904)["DistrictName"] == "Bengaluru City"

    def test_a_missing_unit_is_still_none(self, monkeypatch):
        ref = self._reference([])
        monkeypatch.setattr(ref, "units", lambda: [{"UnitID": "2023", "UnitName": "Market PS"}])
        assert ref.unit(9999) is None
