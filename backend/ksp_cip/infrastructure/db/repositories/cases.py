"""Case read models.

Every query here is parameterized. User-supplied text never reaches SQL as
text: slots are resolved to integer ids by the reference repository first, and
the only free-text predicate is a bound ``LIKE`` parameter.

Authorization is applied *inside* the SQL (unit-subtree predicate), not after
the fact, so a bug in a caller cannot leak rows (architecture §12.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Sequence

from ....domain.models import CaseSummary, PersonRecord, UnitScope
from ....domain.ports import DataStore

CASE_SELECT = """
SELECT
    c.CaseMasterID            AS case_master_id,
    c.CrimeNo                 AS crime_no,
    c.CaseNo                  AS case_no,
    c.CrimeRegisteredDate     AS crime_registered_date,
    c.IncidentFromDate        AS incident_from_date,
    c.IncidentToDate          AS incident_to_date,
    c.InfoReceivedPSDate      AS info_received_ps_date,
    c.PoliceStationID         AS police_station_id,
    c.CaseCategoryID          AS case_category_id,
    c.GravityOffenceID        AS gravity_id,
    c.CrimeMajorHeadID        AS crime_major_head_id,
    c.CrimeMinorHeadID        AS crime_minor_head_id,
    c.CaseStatusID            AS status_id,
    c.CourtID                 AS court_id,
    c.latitude                AS latitude,
    c.longitude               AS longitude,
    c.BriefFacts              AS brief_facts,
    c.cip_brief_facts_kn      AS brief_facts_kn
FROM curated_CaseMaster c
"""


#: The portable spelling of "return no rows".
#:
#: ``WHERE 1 = 0`` is the obvious form and SQLite accepts it, but ZCQL rejects
#: it outright ("Syntax error in given query"). A primary key is never null, so
#: this predicate is always false and is valid in both dialects.
MATCH_NOTHING = " WHERE c.CaseMasterID IS NULL"


def in_clause(prefix: str, values: Sequence[Any]) -> tuple[str, dict[str, Any]]:
    """Build a safe ``IN (...)`` fragment with generated parameter names."""
    names = [f"{prefix}{i}" for i in range(len(values))]
    fragment = ", ".join(f":{n}" for n in names)
    return fragment, dict(zip(names, values))


@dataclass(slots=True)
class CaseFilter:
    unit_ids: Sequence[int] | None = None
    district_ids: Sequence[int] | None = None
    crime_sub_head_ids: Sequence[int] | None = None
    crime_head_ids: Sequence[int] | None = None
    status_ids: Sequence[int] | None = None
    category_ids: Sequence[int] | None = None
    gravity_ids: Sequence[int] | None = None
    case_master_ids: Sequence[int] | None = None
    crime_nos: Sequence[str] | None = None
    date_from: date | None = None
    date_to: date | None = None
    text_contains: str | None = None
    limit: int = 25
    offset: int = 0
    order: str = "recent"
    extra_flags: dict[str, Any] = field(default_factory=dict)


class CaseRepository:
    """Case reads, with display names resolved from cache rather than joined.

    The case select used to carry eight ``LEFT JOIN``s, every one of them
    turning an id into a label (``DistrictID`` -> "Bengaluru Urban"). Catalyst
    permits at most four joins per ZCQL query, so that query could not run
    there at all -- and on any backend it was asking the database to re-read a
    few hundred reference rows on every search.

    ``ReferenceRepository`` already holds all of those tables in memory, warmed
    at startup and invalidated on publication, so the labels are attached after
    the query instead. Filtering and authorization stay in SQL: the scope
    predicate is on ``curated_CaseMaster.PoliceStationID``, a base-table
    column, so removing the joins does not move an authorization decision out
    of the database.

    ``reference`` is optional so existing callers and tests keep working; when
    it is absent, rows come back with ids and no labels.
    """

    def __init__(self, store: DataStore, reference: Any | None = None) -> None:
        self._store = store
        self._reference = reference

    # ------------------------------------------------------- label resolution
    def _labels(self) -> dict[str, dict[Any, Any]]:
        """Build id -> label maps from the reference cache (all in memory)."""
        ref = self._reference
        if ref is None:
            return {}

        def index(rows: Iterable[dict[str, Any]], key: str, value: str) -> dict[Any, Any]:
            out: dict[Any, Any] = {}
            for row in rows:
                if row.get(key) is not None:
                    out[int(row[key])] = row.get(value)
            return out

        units = {int(u["UnitID"]): u for u in ref.units() if u.get("UnitID") is not None}
        return {
            "units": units,
            "districts": index(ref.districts(), "DistrictID", "DistrictName"),
            "categories": index(ref.case_categories(), "CaseCategoryID", "LookupValue"),
            "gravity": index(ref.gravity_levels(), "GravityOffenceID", "LookupValue"),
            "heads": index(ref.crime_heads(), "CrimeHeadID", "CrimeGroupName"),
            "sub_heads": index(ref.crime_sub_heads(), "CrimeSubHeadID", "CrimeHeadName"),
            "statuses": index(ref.case_statuses(), "CaseStatusID", "CaseStatusName"),
            "courts": index(ref.courts(), "CourtID", "CourtName"),
        }

    def _decorate(self, row: dict[str, Any], labels: dict[str, dict[Any, Any]]) -> dict[str, Any]:
        if not labels:
            return row
        station_id = row.get("police_station_id")
        unit = labels["units"].get(int(station_id)) if station_id is not None else None
        district_id = unit.get("DistrictID") if unit else None
        enriched = dict(row)
        enriched["police_station_name"] = unit.get("UnitName") if unit else None
        enriched["district_id"] = district_id
        enriched["district_name"] = (
            labels["districts"].get(int(district_id)) if district_id is not None else None
        )
        for field_name, source, key in (
            ("case_category", "categories", "case_category_id"),
            ("gravity", "gravity", "gravity_id"),
            ("crime_head", "heads", "crime_major_head_id"),
            ("crime_sub_head", "sub_heads", "crime_minor_head_id"),
            ("status", "statuses", "status_id"),
            ("court_name", "courts", "court_id"),
        ):
            value = row.get(key)
            enriched[field_name] = labels[source].get(int(value)) if value is not None else None
        return enriched

    def _station_ids_for_district(self, district_id: int) -> list[int]:
        """Police stations in a district, from cache when one is available."""
        if self._reference is not None:
            return sorted(self._reference.unit_ids_for_district(district_id))
        rows = self._store.query(
            "SELECT UnitID FROM curated_Unit WHERE DistrictID = :d", {"d": district_id}
        )
        return [int(r["UnitID"]) for r in rows if r.get("UnitID") is not None]

    # ---------------------------------------------------------------- build
    def _where(self, filters: CaseFilter, scope: UnitScope) -> tuple[str, dict[str, Any]]:
        clauses: list[str] = []
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
            # Was `u.DistrictID IN (...)`, the one predicate that needed the
            # Unit join. A case belongs to a district exactly when its police
            # station does, so the district expands to its stations first --
            # same rows, no join. Falls back to the join-free equivalent of
            # "matches nothing" when the district has no stations.
            station_ids: list[int] = []
            for district_id in filters.district_ids:
                station_ids.extend(self._station_ids_for_district(int(district_id)))
            if not station_ids:
                return MATCH_NOTHING, {}
            fragment, extra = in_clause("f_d", sorted(set(station_ids)))
            clauses.append(f"c.PoliceStationID IN ({fragment})")
            params.update(extra)
        if filters.crime_sub_head_ids:
            fragment, extra = in_clause("f_sh", list(filters.crime_sub_head_ids))
            clauses.append(f"c.CrimeMinorHeadID IN ({fragment})")
            params.update(extra)
        if filters.crime_head_ids:
            fragment, extra = in_clause("f_h", list(filters.crime_head_ids))
            clauses.append(f"c.CrimeMajorHeadID IN ({fragment})")
            params.update(extra)
        if filters.status_ids:
            fragment, extra = in_clause("f_st", list(filters.status_ids))
            clauses.append(f"c.CaseStatusID IN ({fragment})")
            params.update(extra)
        if filters.category_ids:
            fragment, extra = in_clause("f_cat", list(filters.category_ids))
            clauses.append(f"c.CaseCategoryID IN ({fragment})")
            params.update(extra)
        if filters.gravity_ids:
            fragment, extra = in_clause("f_g", list(filters.gravity_ids))
            clauses.append(f"c.GravityOffenceID IN ({fragment})")
            params.update(extra)
        if filters.case_master_ids:
            fragment, extra = in_clause("f_id", list(filters.case_master_ids))
            clauses.append(f"c.CaseMasterID IN ({fragment})")
            params.update(extra)
        if filters.crime_nos:
            fragment, extra = in_clause("f_cn", [str(v) for v in filters.crime_nos])
            clauses.append(f"c.CrimeNo IN ({fragment})")
            params.update(extra)
        if filters.date_from:
            clauses.append("c.CrimeRegisteredDate >= :date_from")
            params["date_from"] = filters.date_from.isoformat()
        if filters.date_to:
            clauses.append("c.CrimeRegisteredDate <= :date_to")
            params["date_to"] = filters.date_to.isoformat()
        if filters.text_contains:
            clauses.append("(c.BriefFacts LIKE :text_contains OR c.CrimeNo LIKE :text_contains)")
            params["text_contains"] = f"%{filters.text_contains}%"

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    # --------------------------------------------------------------- search
    def search(self, filters: CaseFilter, scope: UnitScope) -> list[CaseSummary]:
        where, params = self._where(filters, scope)
        order = {
            "recent": "ORDER BY c.CrimeRegisteredDate DESC, c.CaseMasterID DESC",
            "oldest": "ORDER BY c.CrimeRegisteredDate ASC, c.CaseMasterID ASC",
            "id": "ORDER BY c.CaseMasterID ASC",
        }.get(filters.order, "ORDER BY c.CrimeRegisteredDate DESC, c.CaseMasterID DESC")
        params["limit"] = max(1, filters.limit)
        params["offset"] = max(0, filters.offset)
        rows = self._store.query(f"{CASE_SELECT}{where} {order} LIMIT :limit OFFSET :offset", params)
        labels = self._labels()
        return [_to_summary(self._decorate(row, labels)) for row in rows]

    def count(self, filters: CaseFilter, scope: UnitScope) -> int:
        where, params = self._where(filters, scope)
        # No Unit join: _where now expands a district filter to its station
        # ids, so every predicate is on curated_CaseMaster itself.
        sql = "SELECT COUNT(*) AS n FROM curated_CaseMaster c" + where
        rows = self._store.query(sql, params)
        return int(rows[0]["n"]) if rows else 0

    def by_id(self, case_master_id: int, scope: UnitScope) -> CaseSummary | None:
        results = self.search(CaseFilter(case_master_ids=[case_master_id], limit=1), scope)
        return results[0] if results else None

    def by_crime_no(self, crime_no: str, scope: UnitScope) -> CaseSummary | None:
        results = self.search(CaseFilter(crime_nos=[crime_no], limit=1), scope)
        return results[0] if results else None

    def by_ids(self, case_master_ids: Sequence[int], scope: UnitScope) -> list[CaseSummary]:
        if not case_master_ids:
            return []
        return self.search(CaseFilter(case_master_ids=list(case_master_ids), limit=len(case_master_ids)), scope)

    # -------------------------------------------------------------- children
    def accused_for_cases(self, case_ids: Sequence[int]) -> list[PersonRecord]:
        return self._people(
            case_ids,
            "SELECT a.AccusedMasterID AS rid, a.CaseMasterID, a.AccusedName AS name, a.AgeYear, a.GenderID,"
            " a.PersonID AS person_ref, c.CrimeNo FROM curated_Accused a"
            " JOIN curated_CaseMaster c ON c.CaseMasterID = a.CaseMasterID",
            "accused",
        )

    def victims_for_cases(self, case_ids: Sequence[int]) -> list[PersonRecord]:
        return self._people(
            case_ids,
            "SELECT v.VictimMasterID AS rid, v.CaseMasterID, v.VictimName AS name, v.AgeYear, v.GenderID,"
            " v.VictimPolice AS person_ref, c.CrimeNo FROM curated_Victim v"
            " JOIN curated_CaseMaster c ON c.CaseMasterID = v.CaseMasterID",
            "victim",
        )

    def complainants_for_cases(self, case_ids: Sequence[int], *, include_sensitive: bool) -> list[PersonRecord]:
        if not case_ids:
            return []
        fragment, params = in_clause("cid", list(case_ids))
        rows = self._store.query(
            "SELECT cd.ComplainantID AS rid, cd.CaseMasterID, cd.ComplainantName AS name, cd.AgeYear,"
            " cd.GenderID, cd.OccupationID, cd.ReligionID, cd.CasteID, c.CrimeNo,"
            " o.OccupationName, r.ReligionName, ca.caste_master_name"
            " FROM curated_ComplainantDetails cd"
            " JOIN curated_CaseMaster c ON c.CaseMasterID = cd.CaseMasterID"
            " LEFT JOIN curated_OccupationMaster o ON o.OccupationID = cd.OccupationID"
            " LEFT JOIN curated_ReligionMaster r ON r.ReligionID = cd.ReligionID"
            " LEFT JOIN curated_CasteMaster ca ON ca.caste_master_id = cd.CasteID"
            f" WHERE cd.CaseMasterID IN ({fragment}) ORDER BY cd.CaseMasterID, cd.ComplainantID",
            params,
        )
        records: list[PersonRecord] = []
        for row in rows:
            extra: dict[str, Any] = {"occupation": row.get("OccupationName")}
            if include_sensitive:
                # Decrypt-equivalent access point: caller must hold
                # READ_SENSITIVE_DEMOGRAPHICS and the access is audited.
                extra["religion"] = row.get("ReligionName")
                extra["caste"] = row.get("caste_master_name")
            records.append(
                PersonRecord(
                    role="complainant",
                    record_id=int(row["rid"]),
                    case_master_id=int(row["CaseMasterID"]),
                    crime_no=str(row["CrimeNo"]),
                    name=str(row.get("name") or ""),
                    age_year=row.get("AgeYear"),
                    gender_id=_as_text(row.get("GenderID")),
                    extra=extra,
                )
            )
        return records

    def _people(self, case_ids: Sequence[int], sql: str, role: str) -> list[PersonRecord]:
        if not case_ids:
            return []
        fragment, params = in_clause("cid", list(case_ids))
        rows = self._store.query(f"{sql} WHERE {_case_column(role)} IN ({fragment}) ORDER BY rid", params)
        return [
            PersonRecord(
                role=role,  # type: ignore[arg-type]
                record_id=int(row["rid"]),
                case_master_id=int(row["CaseMasterID"]),
                crime_no=str(row["CrimeNo"]),
                name=str(row.get("name") or ""),
                age_year=row.get("AgeYear"),
                gender_id=_as_text(row.get("GenderID")),
                person_ref=_as_text(row.get("person_ref")),
            )
            for row in rows
        ]

    def act_sections_for_cases(self, case_ids: Sequence[int]) -> list[dict[str, Any]]:
        if not case_ids:
            return []
        fragment, params = in_clause("cid", list(case_ids))
        return self._store.query(
            "SELECT asa.CaseMasterID, asa.ActID, asa.SectionID, asa.ActOrderID, asa.SectionOrderID,"
            " a.ShortName AS act_short_name, s.SectionDescription"
            " FROM curated_ActSectionAssociation asa"
            " LEFT JOIN curated_Act a ON a.ActCode = asa.ActID"
            " LEFT JOIN curated_Section s ON s.ActCode = asa.ActID AND s.SectionCode = asa.SectionID"
            f" WHERE asa.CaseMasterID IN ({fragment})"
            " ORDER BY asa.CaseMasterID, asa.ActOrderID, asa.SectionOrderID",
            params,
        )

    def arrests_for_cases(self, case_ids: Sequence[int]) -> list[dict[str, Any]]:
        if not case_ids:
            return []
        fragment, params = in_clause("cid", list(case_ids))
        return self._store.query(
            "SELECT ars.ArrestSurrenderID, ars.CaseMasterID, ars.ArrestSurrenderTypeID, ars.ArrestSurrenderDate,"
            " ars.AccusedMasterID, ars.IOID, ars.PoliceStationID, ars.CourtID, ars.IsAccused,"
            " acc.AccusedName, e.FirstName AS io_name, c.CrimeNo"
            " FROM curated_ArrestSurrender ars"
            " JOIN curated_CaseMaster c ON c.CaseMasterID = ars.CaseMasterID"
            " LEFT JOIN curated_Accused acc ON acc.AccusedMasterID = ars.AccusedMasterID"
            " LEFT JOIN curated_Employee e ON e.EmployeeID = ars.IOID"
            f" WHERE ars.CaseMasterID IN ({fragment}) ORDER BY ars.ArrestSurrenderDate",
            params,
        )

    def chargesheets_for_cases(self, case_ids: Sequence[int]) -> list[dict[str, Any]]:
        if not case_ids:
            return []
        fragment, params = in_clause("cid", list(case_ids))
        return self._store.query(
            "SELECT cs.CSID, cs.CaseMasterID, cs.csdate, cs.cstype, cs.PolicePersonID, c.CrimeNo,"
            " e.FirstName AS officer_name"
            " FROM curated_ChargesheetDetails cs"
            " JOIN curated_CaseMaster c ON c.CaseMasterID = cs.CaseMasterID"
            " LEFT JOIN curated_Employee e ON e.EmployeeID = cs.PolicePersonID"
            f" WHERE cs.CaseMasterID IN ({fragment}) ORDER BY cs.csdate",
            params,
        )

    def officer_for_case(self, case_master_id: int) -> dict[str, Any] | None:
        rows = self._store.query(
            "SELECT e.EmployeeID, e.FirstName, e.KGID, r.RankName, dg.DesignationName"
            " FROM curated_CaseMaster c"
            " LEFT JOIN curated_Employee e ON e.EmployeeID = c.PolicePersonID"
            " LEFT JOIN curated_Rank r ON r.RankID = e.RankID"
            " LEFT JOIN curated_Designation dg ON dg.DesignationID = e.DesignationID"
            " WHERE c.CaseMasterID = :cid",
            {"cid": case_master_id},
        )
        return rows[0] if rows else None

    # ---------------------------------------------------------- person search
    def find_accused_by_name(
        self, name_fragment: str, scope: UnitScope, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"name": f"%{name_fragment.strip()}%", "limit": limit}
        scope_sql = ""
        if not scope.statewide:
            allowed = sorted(scope.unit_ids)
            if not allowed:
                return []
            fragment, scope_params = in_clause("scope_u", allowed)
            scope_sql = f" AND c.PoliceStationID IN ({fragment})"
            params.update(scope_params)
        return self._store.query(
            "SELECT a.AccusedMasterID, a.AccusedName, a.AgeYear, a.GenderID, a.PersonID,"
            " c.CaseMasterID, c.CrimeNo, c.CrimeRegisteredDate, c.PoliceStationID,"
            " u.UnitName, u.DistrictID, d.DistrictName, sh.CrimeHeadName AS crime_sub_head,"
            " g.LookupValue AS gravity"
            " FROM curated_Accused a"
            " JOIN curated_CaseMaster c ON c.CaseMasterID = a.CaseMasterID"
            " LEFT JOIN curated_Unit u ON u.UnitID = c.PoliceStationID"
            " LEFT JOIN curated_District d ON d.DistrictID = u.DistrictID"
            " LEFT JOIN curated_CrimeSubHead sh ON sh.CrimeSubHeadID = c.CrimeMinorHeadID"
            " LEFT JOIN curated_GravityOffence g ON g.GravityOffenceID = c.GravityOffenceID"
            " WHERE a.AccusedName LIKE :name" + scope_sql +
            " ORDER BY c.CrimeRegisteredDate DESC LIMIT :limit",
            params,
        )

    def all_accused(self) -> list[dict[str, Any]]:
        """Full accused projection used by the entity-resolution pipeline."""
        return self._store.query(
            "SELECT a.AccusedMasterID, a.AccusedName, a.AgeYear, a.GenderID, a.CaseMasterID,"
            " c.CrimeNo, c.CrimeRegisteredDate, c.PoliceStationID, c.CrimeMinorHeadID,"
            " c.GravityOffenceID, u.DistrictID"
            " FROM curated_Accused a"
            " JOIN curated_CaseMaster c ON c.CaseMasterID = a.CaseMasterID"
            " LEFT JOIN curated_Unit u ON u.UnitID = c.PoliceStationID"
            " ORDER BY a.AccusedMasterID"
        )

    def all_arrests(self) -> list[dict[str, Any]]:
        """Every arrest/surrender row, for graph construction and priority scoring."""
        return self._store.query(
            "SELECT ArrestSurrenderID, CaseMasterID, ArrestSurrenderTypeID, ArrestSurrenderDate,"
            " AccusedMasterID, IOID, PoliceStationID, CourtID FROM curated_ArrestSurrender",
            {},
        )

    def all_chargesheets(self) -> list[dict[str, Any]]:
        return self._store.query(
            "SELECT CSID, CaseMasterID, csdate, cstype FROM curated_ChargesheetDetails", {}
        )

    def cases_for_graph_build(self) -> list[dict[str, Any]]:
        return self._store.query(
            "SELECT c.CaseMasterID, c.CrimeNo, c.CrimeRegisteredDate, c.PoliceStationID,"
            " c.CrimeMinorHeadID, c.latitude, c.longitude, u.DistrictID"
            " FROM curated_CaseMaster c"
            " LEFT JOIN curated_Unit u ON u.UnitID = c.PoliceStationID"
            " ORDER BY c.CaseMasterID"
        )

    def brief_facts_corpus(self) -> list[dict[str, Any]]:
        return self._store.query(
            "SELECT c.CaseMasterID, c.CrimeNo, c.BriefFacts, c.cip_brief_facts_kn, c.PoliceStationID,"
            " sh.CrimeHeadName AS crime_sub_head, d.DistrictName"
            " FROM curated_CaseMaster c"
            " LEFT JOIN curated_CrimeSubHead sh ON sh.CrimeSubHeadID = c.CrimeMinorHeadID"
            " LEFT JOIN curated_Unit u ON u.UnitID = c.PoliceStationID"
            " LEFT JOIN curated_District d ON d.DistrictID = u.DistrictID"
            " WHERE c.BriefFacts IS NOT NULL AND c.BriefFacts <> ''"
            " ORDER BY c.CaseMasterID"
        )


def _case_column(role: str) -> str:
    return {"accused": "a.CaseMasterID", "victim": "v.CaseMasterID"}[role]


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _to_summary(row: dict[str, Any]) -> CaseSummary:
    from datetime import datetime

    def _date(value: Any) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None

    def _dt(value: Any) -> Any:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    return CaseSummary(
        case_master_id=int(row["case_master_id"]),
        crime_no=str(row["crime_no"]),
        case_no=row.get("case_no"),
        crime_registered_date=_date(row.get("crime_registered_date")),
        incident_from_date=_dt(row.get("incident_from_date")),
        incident_to_date=_dt(row.get("incident_to_date")),
        info_received_ps_date=_dt(row.get("info_received_ps_date")),
        police_station_id=row.get("police_station_id"),
        police_station_name=row.get("police_station_name"),
        district_id=row.get("district_id"),
        district_name=row.get("district_name"),
        case_category=row.get("case_category"),
        gravity=row.get("gravity"),
        crime_head=row.get("crime_head"),
        crime_sub_head=row.get("crime_sub_head"),
        crime_major_head_id=row.get("crime_major_head_id"),
        crime_minor_head_id=row.get("crime_minor_head_id"),
        status=row.get("status"),
        court_name=row.get("court_name"),
        latitude=row.get("latitude"),
        longitude=row.get("longitude"),
        brief_facts=row.get("brief_facts"),
        brief_facts_kn=row.get("brief_facts_kn"),
    )


def rows_to_case_summaries(rows: Iterable[dict[str, Any]]) -> list[CaseSummary]:
    return [_to_summary(row) for row in rows]
