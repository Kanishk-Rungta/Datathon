"""Repository for ext_socioeconomic_indicator — district-level socio-economic reference data.

All data in this table is a synthetic approximation drawn from publicly available
Karnataka census and planning commission estimates. It is intentionally labelled
data_quality='synthetic' so every downstream renderer and report header can state
the source honestly.

The ext_ prefix follows the established schema convention: anything prefixed ext_
is NOT part of the organiser's FIR schema and is surfaced to users as an explicitly
marked extension.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ....domain.ports import DataStore

CENSUS_YEAR = 2011  # Default reference year matching synthetic data generation


class SocioEconomicRepository:
    """Read/write access to ext_socioeconomic_indicator.

    All reads are aggregate-only: this table carries district-level counts and
    ratios, never individual records, so it cannot re-identify anyone.
    """

    def __init__(self, store: DataStore) -> None:
        self._store = store

    # ----------------------------------------------------------------- writes

    def upsert(self, row: dict[str, Any]) -> None:
        """Insert or replace a single district-year indicator row."""
        self._store.execute(
            "INSERT INTO ext_socioeconomic_indicator ("
            " indicator_id, district_id, district_name, census_year, population,"
            " literacy_rate, male_literacy, female_literacy, urbanization_percent,"
            " sex_ratio, population_density, unemployment_rate, poverty_headcount,"
            " migration_inflow_rate, sc_st_percent, per_capita_income_index,"
            " data_source, data_quality, is_extension, created_at"
            ") VALUES ("
            " :indicator_id, :district_id, :district_name, :census_year, :population,"
            " :literacy_rate, :male_literacy, :female_literacy, :urbanization_percent,"
            " :sex_ratio, :population_density, :unemployment_rate, :poverty_headcount,"
            " :migration_inflow_rate, :sc_st_percent, :per_capita_income_index,"
            " :data_source, :data_quality, :is_extension, :created_at"
            ") ON CONFLICT (district_id, census_year) DO UPDATE SET"
            " district_name = excluded.district_name,"
            " population = excluded.population,"
            " literacy_rate = excluded.literacy_rate,"
            " male_literacy = excluded.male_literacy,"
            " female_literacy = excluded.female_literacy,"
            " urbanization_percent = excluded.urbanization_percent,"
            " sex_ratio = excluded.sex_ratio,"
            " population_density = excluded.population_density,"
            " unemployment_rate = excluded.unemployment_rate,"
            " poverty_headcount = excluded.poverty_headcount,"
            " migration_inflow_rate = excluded.migration_inflow_rate,"
            " sc_st_percent = excluded.sc_st_percent,"
            " per_capita_income_index = excluded.per_capita_income_index,"
            " data_source = excluded.data_source,"
            " data_quality = excluded.data_quality",
            row,
        )

    # ----------------------------------------------------------------- reads

    def all_for_year(self, census_year: int = CENSUS_YEAR) -> list[dict[str, Any]]:
        """Return all district indicators for a given census year, ordered by district_id."""
        return self._store.query(
            "SELECT * FROM ext_socioeconomic_indicator"
            " WHERE census_year = :year ORDER BY district_id",
            {"year": census_year},
        )

    def for_district(self, district_id: int, census_year: int = CENSUS_YEAR) -> dict[str, Any] | None:
        """Return one district's indicators, or None if not loaded yet."""
        rows = self._store.query(
            "SELECT * FROM ext_socioeconomic_indicator"
            " WHERE district_id = :district_id AND census_year = :year LIMIT 1",
            {"district_id": district_id, "year": census_year},
        )
        return rows[0] if rows else None

    def available_years(self) -> list[int]:
        """List of census years that have data loaded."""
        rows = self._store.query(
            "SELECT DISTINCT census_year FROM ext_socioeconomic_indicator ORDER BY census_year"
        )
        return [int(row["census_year"]) for row in rows]

    def count(self) -> int:
        rows = self._store.query(
            "SELECT COUNT(*) AS n FROM ext_socioeconomic_indicator"
        )
        return int(rows[0]["n"]) if rows else 0

    def indicators_by_district(self, *, census_year: int = CENSUS_YEAR) -> dict[int, dict[str, Any]]:
        """Return a mapping of district_id → indicator row for efficient join in the correlator."""
        rows = self.all_for_year(census_year)
        return {int(row["district_id"]): row for row in rows}
