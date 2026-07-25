"""CrimeNo grammar, geography and name normalisation."""

import pytest

from ksp_cip.domain.errors import ValidationError
from ksp_cip.domain.value_objects import CrimeNo, GeoPoint, normalize_person_name


class TestCrimeNo:
    def test_parses_the_documented_format(self):
        crime_no = CrimeNo.parse("104430006202600001")
        assert crime_no.category_code == 1
        assert crime_no.district_id == 443
        assert crime_no.station_id == 6
        assert crime_no.year == 2026
        assert crime_no.serial == 1

    def test_case_no_is_the_last_nine_digits(self):
        assert CrimeNo.parse("104430006202600001").case_no == "202600001"

    @pytest.mark.parametrize("category,prefix", [(1, "1"), (3, "3"), (4, "4"), (8, "8")])
    def test_every_documented_category_round_trips(self, category, prefix):
        built = CrimeNo.build(category_code=category, district_id=443, station_id=6,
                              year=2026, serial=1)
        assert built.raw.startswith(prefix)
        assert CrimeNo.parse(built.raw) == built

    @pytest.mark.parametrize("bad", ["", "123", "abcdefghijklmnopqr", "10443000620260000",
                                     "1044300062026000012"])
    def test_malformed_numbers_are_rejected(self, bad):
        assert CrimeNo.try_parse(bad) is None
        with pytest.raises(ValidationError):
            CrimeNo.parse(bad)

    def test_out_of_range_components_are_rejected(self):
        with pytest.raises(ValidationError):
            CrimeNo.build(category_code=12, district_id=443, station_id=6, year=2026, serial=1)
        with pytest.raises(ValidationError):
            CrimeNo.build(category_code=1, district_id=443, station_id=6, year=2026, serial=100_000)


class TestGeoPoint:
    def test_haversine_matches_a_known_distance(self):
        bengaluru = GeoPoint(12.9716, 77.5946)
        mysuru = GeoPoint(12.2958, 76.6394)
        metres = bengaluru.distance_metres(mysuru)
        assert 125_000 < metres < 135_000  # ~130 km by great circle

    def test_grid_cells_agree_within_a_cell_and_differ_across_one(self):
        origin = GeoPoint(12.9716, 77.5946)
        nearby = GeoPoint(12.9718, 77.5948)
        far = GeoPoint(13.0716, 77.6946)
        assert origin.grid_cell(750) == nearby.grid_cell(750)
        assert origin.grid_cell(750) != far.grid_cell(750)

    def test_karnataka_bounds_are_enforced(self):
        assert GeoPoint(12.9716, 77.5946).within_karnataka
        assert not GeoPoint(28.6139, 77.2090).within_karnataka  # Delhi


class TestNameNormalisation:
    def test_honorifics_and_case_are_removed(self):
        assert normalize_person_name("Sri. RAMESH Gowda") == normalize_person_name("ramesh gowda")

    def test_alias_markers_are_dropped(self):
        assert normalize_person_name("Ramesh @ Ramu Gowda").startswith("ramesh")

    def test_whitespace_is_collapsed(self):
        assert normalize_person_name("  Ramesh   Gowda  ") == "ramesh gowda"
