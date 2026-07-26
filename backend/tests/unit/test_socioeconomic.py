"""Unit tests for ext_socioeconomic_indicator data layer, repository, generator, and correlator."""

from __future__ import annotations

from typing import Any

from ksp_cip.application.analytics.socioeconomic import (
    INDICATOR_LABELS,
    MIN_DISTRICTS,
    SocioEconomicCorrelator,
    _pearson_r,
)
from ksp_cip.application.pipeline.generators.masters import generate_masters
from ksp_cip.application.pipeline.generators.socioeconomic import generate_socioeconomic_indicators
from ksp_cip.domain.models import UnitScope
from ksp_cip.infrastructure.db.migrations import apply_migrations
from ksp_cip.infrastructure.db.repositories.analytics import AnalyticsRepository
from ksp_cip.infrastructure.db.repositories.socioeconomic import SocioEconomicRepository
from ksp_cip.infrastructure.db.sqlite_store import SQLiteDataStore


def _test_store(tmp_path: Any) -> SQLiteDataStore:
    db_file = tmp_path / "test_socioeconomic.db"
    store = SQLiteDataStore(db_file)
    apply_migrations(store)
    return store


def test_socioeconomic_generator() -> None:
    import random

    rng = random.Random(42)
    masters = generate_masters(rng)
    districts = masters.districts
    rows = generate_socioeconomic_indicators(districts, census_year=2011)

    assert len(rows) == len(districts)
    assert len(rows) == 31

    for row in rows:
        assert row["census_year"] == 2011
        assert row["data_quality"] == "synthetic"
        assert row["is_extension"] == 1
        assert row["population"] > 100_000
        assert 0.0 <= row["literacy_rate"] <= 100.0
        assert 0.0 <= row["urbanization_percent"] <= 100.0
        assert 0.0 <= row["unemployment_rate"] <= 100.0


import pytest


def test_socioeconomic_repository(tmp_path: Any) -> None:
    store = _test_store(tmp_path)
    repo = SocioEconomicRepository(store)

    assert repo.count() == 0

    import random

    rng = random.Random(42)
    masters = generate_masters(rng)
    rows = generate_socioeconomic_indicators(masters.districts, census_year=2011)

    for row in rows:
        repo.upsert(row)

    assert repo.count() == 31
    loaded = repo.all_for_year(2011)
    assert len(loaded) == 31

    # Bengaluru City is index 4 in KARNATAKA_DISTRICTS -> DistrictID 2904
    bengaluru = repo.for_district(2904, census_year=2011)
    assert bengaluru is not None
    assert bengaluru["district_name"] == "Bengaluru City"
    assert bengaluru["urbanization_percent"] > 90.0

    by_district = repo.indicators_by_district(census_year=2011)
    assert len(by_district) == 31
    assert 2904 in by_district


def test_pearson_r_math() -> None:
    # Perfect positive correlation
    assert _pearson_r([1.0, 2.0, 3.0, 4.0, 5.0], [2.0, 4.0, 6.0, 8.0, 10.0]) == pytest.approx(1.0)
    # Perfect negative correlation
    assert _pearson_r([1.0, 2.0, 3.0, 4.0, 5.0], [10.0, 8.0, 6.0, 4.0, 2.0]) == pytest.approx(-1.0)
    # Constant array (zero variance) returns None
    assert _pearson_r([1.0, 1.0, 1.0], [2.0, 3.0, 4.0]) is None


def test_socioeconomic_correlator_insufficient_data(tmp_path: Any) -> None:
    store = _test_store(tmp_path)
    analytics = AnalyticsRepository(store)
    socio = SocioEconomicRepository(store)

    correlator = SocioEconomicCorrelator(analytics, socio)
    scope = UnitScope(statewide=True, unit_ids=tuple(range(1, 100)))

    # Without loaded indicators, return empty correlations gracefully
    result = correlator.correlate(scope, census_year=2011)
    assert result.district_count == 0
    assert result.correlations == []
    assert "Only 0 district(s) had complete" in result.trace.description


def test_socioeconomic_correlator_with_data(tmp_path: Any) -> None:
    store = _test_store(tmp_path)
    store.execute("PRAGMA foreign_keys = OFF", {})
    analytics = AnalyticsRepository(store)
    socio = SocioEconomicRepository(store)

    # Seed master districts and units into test DB
    import random

    rng = random.Random(42)
    masters = generate_masters(rng)

    for d in masters.districts:
        store.execute(
            "INSERT INTO curated_District (DistrictID, DistrictName, StateID, Active) "
            "VALUES (:DistrictID, :DistrictName, :StateID, :Active)",
            d,
        )
    for u in masters.units:
        store.execute(
            "INSERT INTO curated_Unit (UnitID, UnitName, TypeID, ParentUnit, DistrictID, Active) "
            "VALUES (:UnitID, :UnitName, :TypeID, :ParentUnit, :DistrictID, :Active)",
            u,
        )

    # Seed socio-economic indicators
    rows = generate_socioeconomic_indicators(masters.districts, census_year=2011)
    for row in rows:
        socio.upsert(row)

    # Seed mock cases for districts
    now_iso = "2026-05-15T10:00:00"
    for d in masters.districts[:15]:
        district_id = int(d["DistrictID"])
        unit_rows = store.query("SELECT UnitID FROM curated_Unit WHERE DistrictID = :d LIMIT 1", {"d": district_id})
        if not unit_rows:
            continue
        unit_id = unit_rows[0]["UnitID"]
        case_count = (district_id % 10 + 1) * 15
        for i in range(case_count):
            store.execute(
                "INSERT INTO curated_CaseMaster (CrimeNo, PoliceStationID, CrimeRegisteredDate) "
                "VALUES (:c, :u, :d)",
                {"c": f"FIR{district_id}{i:04d}", "u": unit_id, "d": now_iso},
            )

    correlator = SocioEconomicCorrelator(analytics, socio)
    scope = UnitScope(statewide=True, unit_ids=tuple(u["UnitID"] for u in masters.units))

    result = correlator.correlate(scope, census_year=2011)

    assert result.district_count >= MIN_DISTRICTS
    assert len(result.correlations) > 0
    assert result.data_quality == "synthetic"
    assert result.trace.operation == "socioeconomic_correlation"

    # Check top correlation shape
    c0 = result.correlations[0]
    assert c0.indicator in INDICATOR_LABELS
    assert -1.0 <= c0.pearson_r <= 1.0
    assert c0.district_count >= MIN_DISTRICTS
    assert "association" in c0.interpretation

