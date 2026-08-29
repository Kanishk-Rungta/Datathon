"""Unit tests for spatio-temporal predictive forecasting model."""

from __future__ import annotations

from datetime import date
from typing import Any

from ksp_cip.application.analytics.spatiotemporal import (
    SpatioTemporalForecaster,
    _haversine_distance,
    _lat_lon_to_grid,
    _poisson_cdf_approx,
)
from ksp_cip.domain.models import UnitScope
from ksp_cip.infrastructure.db.migrations import apply_migrations
from ksp_cip.infrastructure.db.repositories.analytics import AggregateFilter, AnalyticsRepository
from ksp_cip.infrastructure.db.sqlite_store import SQLiteDataStore


def _test_store(tmp_path: Any) -> SQLiteDataStore:
    db_file = tmp_path / "test_spatiotemporal.db"
    store = SQLiteDataStore(db_file)
    apply_migrations(store)
    store.execute("PRAGMA foreign_keys = OFF", {})
    return store


def test_haversine_distance_and_grid_math() -> None:
    # Bengaluru coordinates
    lat1, lon1 = 12.9716, 77.5946
    # Mysuru coordinates (~130 km away)
    lat2, lon2 = 12.2958, 76.6394

    dist = _haversine_distance(lat1, lon1, lat2, lon2)
    assert 120_000.0 <= dist <= 140_000.0

    r1, c1 = _lat_lon_to_grid(lat1, lon1, grid_metres=1000)
    r2, c2 = _lat_lon_to_grid(lat1 + 0.005, lon1 + 0.005, grid_metres=1000)
    assert isinstance(r1, int) and isinstance(c1, int)


def test_poisson_cdf() -> None:
    # Poisson(mu=0.5), P(X <= 2) should be close to 1.0
    p = _poisson_cdf_approx(2, 0.5)
    assert 0.95 <= p <= 1.0

    # Poisson(mu=5.0), P(X <= 2) should be relatively small
    p_high = _poisson_cdf_approx(2, 5.0)
    assert 0.05 <= p_high <= 0.25


def test_spatiotemporal_forecaster_empty_data(tmp_path: Any) -> None:
    store = _test_store(tmp_path)
    analytics = AnalyticsRepository(store)
    forecaster = SpatioTemporalForecaster(analytics, grid_metres=750)

    scope = UnitScope(statewide=True, unit_ids=tuple(range(1, 100)))
    filters = AggregateFilter()

    result = forecaster.predict(filters, scope, horizon_days=30, as_of=date(2026, 5, 15))
    assert result.horizon_days == 30
    assert result.total_historical_cases == 0
    assert result.predicted_cells == []
    assert result.trace.operation == "spatiotemporal_forecast"


def test_spatiotemporal_forecaster_with_geo_data(tmp_path: Any) -> None:
    store = _test_store(tmp_path)
    analytics = AnalyticsRepository(store)

    # Seed 30 geo-coded cases in curated_CaseMaster
    for i in range(30):
        lat = 12.9716 + (i % 3) * 0.005
        lon = 77.5946 + (i % 4) * 0.005
        d_str = f"2026-04-{(i % 25) + 1:02d}T10:00:00"
        store.execute(
            "INSERT INTO curated_CaseMaster (CrimeNo, PoliceStationID, CrimeRegisteredDate, Latitude, Longitude) "
            "VALUES (:c, :u, :d, :lat, :lon)",
            {"c": f"STFIR{i:03d}", "u": 2001, "d": d_str, "lat": lat, "lon": lon},
        )

    forecaster = SpatioTemporalForecaster(analytics, grid_metres=1000)
    scope = UnitScope(statewide=True, unit_ids=(2001,))
    filters = AggregateFilter()

    result = forecaster.predict(filters, scope, horizon_days=30, historical_days=60, as_of=date(2026, 5, 1))

    assert result.total_historical_cases == 30
    assert len(result.predicted_cells) > 0
    assert result.projected_total_cases > 0.0
    assert result.model_name == "Spatial-Poisson-Holt-Winters"

    c0 = result.predicted_cells[0]
    assert c0.expected_count > 0.0
    assert c0.lower_bound <= c0.expected_count <= c0.upper_bound
    assert 0.0 <= c0.hotspot_probability <= 1.0
    assert c0.risk_level in {"low", "medium", "high"}
