"""Aggregate read models for the analytics engine.

Every method returns *rows of counts*, never narrative. All arithmetic beyond
``COUNT``/``GROUP BY`` (baselines, z-scores, growth rates) happens in
``application.analytics`` where it is unit-tested against known fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

from ....domain.models import UnitScope
from ....domain.ports import DataStore
from .cases import MATCH_NOTHING, in_clause


@dataclass(slots=True)
class AggregateFilter:
    unit_ids: Sequence[int] | None = None
    district_ids: Sequence[int] | None = None
    crime_sub_head_ids: Sequence[int] | None = None
    crime_head_ids: Sequence[int] | None = None
    date_from: date | None = None
    date_to: date | None = None


class EventCalendarRepository:
    """Reference events (festivals, large gatherings) for window comparison.

    Two governance rules are enforced in the query, not in the caller:

    * only rows with ``approval_status = 'approved'`` are ever returned, so an
      unreviewed event cannot reach an answer;
    * the table is CIP-derived reference data (``cip_`` prefix) and is never
      joined into the organiser's FIR schema.
    """

    APPROVED = "approved"

    def __init__(self, store: DataStore) -> None:
        self._store = store

    def upsert(self, event: dict[str, Any]) -> None:
        self._store.execute(
            "INSERT INTO cip_event_calendar (event_id, event_name, event_type, date_from, date_to,"
            " district_id, unit_id, source, data_quality, approval_status, created_at)"
            " VALUES (:event_id, :event_name, :event_type, :date_from, :date_to,"
            " :district_id, :unit_id, :source, :data_quality, :approval_status, :created_at)"
            " ON CONFLICT (event_id) DO UPDATE SET event_name = excluded.event_name,"
            " event_type = excluded.event_type, date_from = excluded.date_from,"
            " date_to = excluded.date_to, district_id = excluded.district_id,"
            " unit_id = excluded.unit_id, source = excluded.source,"
            " data_quality = excluded.data_quality, approval_status = excluded.approval_status",
            event,
        )

    def approved_events(self, *, district_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"status": self.APPROVED, "limit": limit}
        clause = ""
        if district_id is not None:
            clause = " AND (district_id IS NULL OR district_id = :district_id)"
            params["district_id"] = district_id
        return self._store.query(
            "SELECT * FROM cip_event_calendar WHERE approval_status = :status"
            + clause + " ORDER BY date_from DESC LIMIT :limit",
            params,
        )

    def by_name(self, name: str) -> dict[str, Any] | None:
        rows = self._store.query(
            "SELECT * FROM cip_event_calendar WHERE approval_status = :status"
            " AND LOWER(event_name) = :name LIMIT 1",
            {"status": self.APPROVED, "name": name.casefold()},
        )
        return rows[0] if rows else None

    def count(self) -> int:
        rows = self._store.query("SELECT COUNT(*) AS n FROM cip_event_calendar")
        return int(rows[0]["n"]) if rows else 0


def _rollup(
    rows: Sequence[dict[str, Any]],
    source: str,
    start: int,
    end: int,
    out_key: str,
    carry: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Roll per-date counts up to a coarser period, summing ``case_count``.

    The obvious way to write these queries is ``GROUP BY substr(date, 1, 7)``.
    ZCQL rejects it — *"Aggregate function cannot have more than one column"* —
    because it accepts only single-argument functions. Grouping by the raw
    date in SQL and folding here keeps one query per backend rather than two
    dialects, and the database still does the heavy aggregation: the rows
    crossing the wire number one per distinct date, not one per case.
    """
    buckets: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        raw = row.get(source)
        if raw is None:
            continue
        bucket_value = str(raw)[start:end]
        if not bucket_value:
            continue
        key = (bucket_value, *(row.get(name) for name in carry))
        entry = buckets.get(key)
        if entry is None:
            entry = {out_key: bucket_value}
            for name in carry:
                entry[name] = row.get(name)
            entry["case_count"] = 0
            buckets[key] = entry
        entry["case_count"] += int(row.get("case_count") or 0)
    return [buckets[key] for key in sorted(buckets, key=lambda k: tuple(str(p) for p in k))]


class AnalyticsRepository:
    def __init__(self, store: DataStore) -> None:
        self._store = store

    def _predicate(self, filters: AggregateFilter, scope: UnitScope) -> tuple[str, dict[str, Any]]:
        clauses: list[str] = ["c.CrimeRegisteredDate IS NOT NULL"]
        params: dict[str, Any] = {}
        if not scope.statewide:
            allowed = sorted(scope.unit_ids)
            if not allowed:
                return MATCH_NOTHING, {}
            fragment, scope_params = in_clause("scope_u", allowed)
            clauses.append(f"c.PoliceStationID IN ({fragment})")
            params.update(scope_params)
        if filters.unit_ids:
            fragment, extra = in_clause("f_u", list(filters.unit_ids))
            clauses.append(f"c.PoliceStationID IN ({fragment})")
            params.update(extra)
        if filters.district_ids:
            fragment, extra = in_clause("f_d", list(filters.district_ids))
            clauses.append(f"u.DistrictID IN ({fragment})")
            params.update(extra)
        if filters.crime_sub_head_ids:
            fragment, extra = in_clause("f_sh", list(filters.crime_sub_head_ids))
            clauses.append(f"c.CrimeMinorHeadID IN ({fragment})")
            params.update(extra)
        if filters.crime_head_ids:
            fragment, extra = in_clause("f_h", list(filters.crime_head_ids))
            clauses.append(f"c.CrimeMajorHeadID IN ({fragment})")
            params.update(extra)
        if filters.date_from:
            clauses.append("c.CrimeRegisteredDate >= :date_from")
            params["date_from"] = filters.date_from.isoformat()
        if filters.date_to:
            clauses.append("c.CrimeRegisteredDate <= :date_to")
            params["date_to"] = filters.date_to.isoformat()
        return " WHERE " + " AND ".join(clauses), params

    _FROM = (
        " FROM curated_CaseMaster c"
        " LEFT JOIN curated_Unit u ON u.UnitID = c.PoliceStationID"
        " LEFT JOIN curated_District d ON d.DistrictID = u.DistrictID"
        " LEFT JOIN curated_CrimeSubHead sh ON sh.CrimeSubHeadID = c.CrimeMinorHeadID"
        " LEFT JOIN curated_CrimeHead h ON h.CrimeHeadID = c.CrimeMajorHeadID"
    )

    # ------------------------------------------------------------ timeseries
    def monthly_counts(self, filters: AggregateFilter, scope: UnitScope) -> list[dict[str, Any]]:
        where, params = self._predicate(filters, scope)
        rows = self._store.query(
            "SELECT c.CrimeRegisteredDate AS period_date, COUNT(*) AS case_count"
            + self._FROM + where + " GROUP BY c.CrimeRegisteredDate ORDER BY c.CrimeRegisteredDate",
            params,
        )
        return _rollup(rows, "period_date", 0, 7, "period")

    def monthly_counts_by_sub_head(self, filters: AggregateFilter, scope: UnitScope) -> list[dict[str, Any]]:
        where, params = self._predicate(filters, scope)
        rows = self._store.query(
            "SELECT c.CrimeRegisteredDate AS period_date, c.CrimeMinorHeadID AS sub_head_id,"
            " sh.CrimeHeadName AS sub_head, COUNT(*) AS case_count"
            + self._FROM + where + " GROUP BY c.CrimeRegisteredDate, c.CrimeMinorHeadID, sh.CrimeHeadName ORDER BY c.CrimeRegisteredDate",
            params,
        )
        return _rollup(rows, "period_date", 0, 7, "period", carry=("sub_head_id", "sub_head"))

    def daily_counts(self, filters: AggregateFilter, scope: UnitScope) -> list[dict[str, Any]]:
        where, params = self._predicate(filters, scope)
        return self._store.query(
            "SELECT c.CrimeRegisteredDate AS period, COUNT(*) AS case_count"
            + self._FROM + where + " GROUP BY period ORDER BY period",
            params,
        )

    # --------------------------------------------------------------- breakdown
    def counts_by_sub_head(self, filters: AggregateFilter, scope: UnitScope, limit: int = 20) -> list[dict[str, Any]]:
        where, params = self._predicate(filters, scope)
        params["limit"] = limit
        return self._store.query(
            "SELECT c.CrimeMinorHeadID AS sub_head_id, sh.CrimeHeadName AS sub_head,"
            " h.CrimeGroupName AS crime_head, COUNT(*) AS case_count"
            + self._FROM + where +
            " GROUP BY sub_head_id, sub_head, crime_head ORDER BY case_count DESC LIMIT :limit",
            params,
        )

    def counts_by_district(self, filters: AggregateFilter, scope: UnitScope) -> list[dict[str, Any]]:
        where, params = self._predicate(filters, scope)
        return self._store.query(
            "SELECT u.DistrictID AS district_id, d.DistrictName AS district_name, COUNT(*) AS case_count"
            + self._FROM + where + " GROUP BY district_id, district_name ORDER BY case_count DESC",
            params,
        )

    def counts_by_unit(self, filters: AggregateFilter, scope: UnitScope) -> list[dict[str, Any]]:
        where, params = self._predicate(filters, scope)
        return self._store.query(
            "SELECT c.PoliceStationID AS unit_id, u.UnitName AS unit_name, u.DistrictID AS district_id,"
            " d.DistrictName AS district_name, COUNT(*) AS case_count"
            + self._FROM + where + " GROUP BY unit_id, unit_name, district_id, district_name"
            " ORDER BY case_count DESC",
            params,
        )

    def counts_by_status(self, filters: AggregateFilter, scope: UnitScope) -> list[dict[str, Any]]:
        where, params = self._predicate(filters, scope)
        return self._store.query(
            "SELECT c.CaseStatusID AS status_id, st.CaseStatusName AS status, COUNT(*) AS case_count"
            + self._FROM + " LEFT JOIN curated_CaseStatusMaster st ON st.CaseStatusID = c.CaseStatusID"
            + where + " GROUP BY status_id, status ORDER BY case_count DESC",
            params,
        )

    def counts_by_hour(self, filters: AggregateFilter, scope: UnitScope) -> list[dict[str, Any]]:
        where, params = self._predicate(filters, scope)
        rows = self._store.query(
            "SELECT c.IncidentFromDate AS incident_at, COUNT(*) AS case_count"
            + self._FROM + where + " AND c.IncidentFromDate IS NOT NULL"
            " GROUP BY c.IncidentFromDate ORDER BY c.IncidentFromDate",
            params,
        )
        return _rollup(rows, "incident_at", 11, 13, "hour_of_day")

    # ------------------------------------------------------------------- geo
    def geo_points(self, filters: AggregateFilter, scope: UnitScope, limit: int = 20000) -> list[dict[str, Any]]:
        where, params = self._predicate(filters, scope)
        params["limit"] = limit
        return self._store.query(
            "SELECT c.CaseMasterID AS case_master_id, c.CrimeNo AS crime_no, c.latitude, c.longitude,"
            " c.CrimeRegisteredDate AS registered_date, c.PoliceStationID AS unit_id,"
            " u.DistrictID AS district_id, sh.CrimeHeadName AS sub_head"
            + self._FROM + where + " AND c.latitude IS NOT NULL AND c.longitude IS NOT NULL"
            " ORDER BY c.CrimeRegisteredDate DESC LIMIT :limit",
            params,
        )

    # ---------------------------------------------------------- demographics
    def victim_demographics(self, filters: AggregateFilter, scope: UnitScope) -> list[dict[str, Any]]:
        where, params = self._predicate(filters, scope)
        return self._store.query(
            "SELECT sh.CrimeHeadName AS sub_head, v.GenderID AS gender,"
            " CASE WHEN v.AgeYear IS NULL THEN 'unknown'"
            "      WHEN v.AgeYear < 18 THEN '0-17'"
            "      WHEN v.AgeYear < 30 THEN '18-29'"
            "      WHEN v.AgeYear < 45 THEN '30-44'"
            "      WHEN v.AgeYear < 60 THEN '45-59'"
            "      ELSE '60+' END AS age_band,"
            " COUNT(*) AS record_count"
            + self._FROM + " JOIN curated_Victim v ON v.CaseMasterID = c.CaseMasterID"
            + where + " GROUP BY sub_head, gender, age_band ORDER BY record_count DESC",
            params,
        )

    def complainant_demographics(
        self, filters: AggregateFilter, scope: UnitScope, *, dimension: str
    ) -> list[dict[str, Any]]:
        """Aggregate-only crosstab. ``dimension`` is a whitelisted column."""
        dimension_sql = {
            "occupation": ("o.OccupationName", "LEFT JOIN curated_OccupationMaster o ON o.OccupationID = cd.OccupationID"),
            "religion": ("r.ReligionName", "LEFT JOIN curated_ReligionMaster r ON r.ReligionID = cd.ReligionID"),
            "caste": ("ca.caste_master_name", "LEFT JOIN curated_CasteMaster ca ON ca.caste_master_id = cd.CasteID"),
            "gender": ("cd.GenderID", ""),
            "age_band": (
                "CASE WHEN cd.AgeYear IS NULL THEN 'unknown'"
                " WHEN cd.AgeYear < 18 THEN '0-17' WHEN cd.AgeYear < 30 THEN '18-29'"
                " WHEN cd.AgeYear < 45 THEN '30-44' WHEN cd.AgeYear < 60 THEN '45-59'"
                " ELSE '60+' END",
                "",
            ),
        }
        if dimension not in dimension_sql:
            from ....domain.errors import ValidationError

            raise ValidationError("Unsupported demographic dimension", dimension=dimension)
        expression, join = dimension_sql[dimension]
        where, params = self._predicate(filters, scope)
        return self._store.query(
            f"SELECT {expression} AS dimension_value, sh.CrimeHeadName AS sub_head, COUNT(*) AS record_count"
            + self._FROM
            + " JOIN curated_ComplainantDetails cd ON cd.CaseMasterID = c.CaseMasterID "
            + join
            + where
            + " GROUP BY dimension_value, sub_head ORDER BY record_count DESC",
            params,
        )

    def victim_demographic_dimension(
        self, filters: AggregateFilter, scope: UnitScope, *, dimension: str
    ) -> list[dict[str, Any]]:
        """Aggregate-only crosstab over ``curated_Victim``.

        The organiser's Victim table carries only ``AgeYear``/``GenderID`` —
        no occupation/religion/caste columns exist on it — so only those two
        dimensions are offered here. This is a real schema limit, not an
        oversight: adding those columns would mean redesigning the source
        schema, which the project does not do.
        """
        dimension_sql = {
            "gender": ("v.GenderID", ""),
            "age_band": (
                "CASE WHEN v.AgeYear IS NULL THEN 'unknown'"
                " WHEN v.AgeYear < 18 THEN '0-17' WHEN v.AgeYear < 30 THEN '18-29'"
                " WHEN v.AgeYear < 45 THEN '30-44' WHEN v.AgeYear < 60 THEN '45-59'"
                " ELSE '60+' END",
                "",
            ),
        }
        if dimension not in dimension_sql:
            from ....domain.errors import ValidationError

            raise ValidationError(
                "This dimension is not available for victim records; the source schema only carries "
                "victim age and gender",
                dimension=dimension,
            )
        expression, join = dimension_sql[dimension]
        where, params = self._predicate(filters, scope)
        return self._store.query(
            f"SELECT {expression} AS dimension_value, sh.CrimeHeadName AS sub_head, COUNT(*) AS record_count"
            + self._FROM
            + " JOIN curated_Victim v ON v.CaseMasterID = c.CaseMasterID "
            + join
            + where
            + " GROUP BY dimension_value, sub_head ORDER BY record_count DESC",
            params,
        )

    def total_cases(self, filters: AggregateFilter, scope: UnitScope) -> int:
        where, params = self._predicate(filters, scope)
        rows = self._store.query("SELECT COUNT(*) AS n" + self._FROM + where, params)
        return int(rows[0]["n"]) if rows else 0

    def case_ids_for(self, filters: AggregateFilter, scope: UnitScope, limit: int = 200) -> list[dict[str, Any]]:
        where, params = self._predicate(filters, scope)
        params["limit"] = limit
        return self._store.query(
            "SELECT c.CaseMasterID AS case_master_id, c.CrimeNo AS crime_no"
            + self._FROM + where + " ORDER BY c.CrimeRegisteredDate DESC LIMIT :limit",
            params,
        )

    def counts_between(
        self, filters: AggregateFilter, scope: UnitScope, *, date_from: date, date_to: date
    ) -> int:
        """Case count in an explicit window, reusing the caller's other filters."""
        window = AggregateFilter(
            unit_ids=filters.unit_ids, district_ids=filters.district_ids,
            crime_sub_head_ids=filters.crime_sub_head_ids, crime_head_ids=filters.crime_head_ids,
            date_from=date_from, date_to=date_to,
        )
        return self.total_cases(window, scope)

    def data_coverage(self) -> dict[str, Any]:
        rows = self._store.query(
            "SELECT COUNT(*) AS case_count, MIN(CrimeRegisteredDate) AS first_date,"
            " MAX(CrimeRegisteredDate) AS last_date FROM curated_CaseMaster"
        )
        return rows[0] if rows else {"case_count": 0, "first_date": None, "last_date": None}
