"""Reference-data access: masters, unit hierarchy, fuzzy name resolution.

Master tables are tiny and read constantly, so this repository caches them in
process with an explicit invalidation hook (the Catalyst Cache stand-in).
"""

from __future__ import annotations

import threading
from difflib import SequenceMatcher
from typing import Any

from ....domain.ports import DataStore



def _same_id(left: Any, right: Any) -> bool:
    """Compare two ids that may not share a Python type.

    The Catalyst Data Store returns every column as a string, so a cached
    ``UnitID`` of ``"2023"`` never equals the ``2023`` a caller holds. That
    made ``unit()`` and ``district()`` return None on Catalyst while working on
    SQLite -- a scoped officer's console read "no unit assigned" instead of
    their station, and hotspot cells lost their district label. Authorization
    itself was unaffected (the unit-subtree predicate is built from the closure
    table, not from this lookup), but the label it shows the caller was wrong,
    which is its own kind of failure on a screen about who can see what.
    """
    if left is None or right is None:
        return False
    try:
        return int(left) == int(right)
    except (TypeError, ValueError):
        return str(left) == str(right)


class ReferenceRepository:
    _CACHEABLE = {
        "districts": "SELECT DistrictID, DistrictName, StateID, Active FROM curated_District ORDER BY DistrictName",
        "states": "SELECT StateID, StateName, Active FROM curated_State ORDER BY StateName",
        "units": (
            "SELECT u.UnitID, u.UnitName, u.TypeID, u.ParentUnit, u.StateID, u.DistrictID, u.Active,"
            " u.cip_latitude, u.cip_longitude, d.DistrictName, t.UnitTypeName"
            " FROM curated_Unit u"
            " LEFT JOIN curated_District d ON d.DistrictID = u.DistrictID"
            " LEFT JOIN curated_UnitType t ON t.UnitTypeID = u.TypeID"
            " ORDER BY u.UnitName"
        ),
        "crime_heads": "SELECT CrimeHeadID, CrimeGroupName, Active FROM curated_CrimeHead ORDER BY CrimeGroupName",
        "crime_sub_heads": (
            "SELECT s.CrimeSubHeadID, s.CrimeHeadID, s.CrimeHeadName, s.SeqID, h.CrimeGroupName"
            " FROM curated_CrimeSubHead s"
            " LEFT JOIN curated_CrimeHead h ON h.CrimeHeadID = s.CrimeHeadID"
            " ORDER BY s.SeqID"
        ),
        "case_statuses": "SELECT CaseStatusID, CaseStatusName FROM curated_CaseStatusMaster ORDER BY CaseStatusID",
        "case_categories": (
            "SELECT CaseCategoryID, LookupValue, CategoryCode FROM curated_CaseCategory ORDER BY CaseCategoryID"
        ),
        "gravity": "SELECT GravityOffenceID, LookupValue FROM curated_GravityOffence ORDER BY GravityOffenceID",
        "acts": "SELECT ActCode, ActDescription, ShortName, Active FROM curated_Act ORDER BY ActCode",
        "occupations": "SELECT OccupationID, OccupationName FROM curated_OccupationMaster ORDER BY OccupationID",
        "religions": "SELECT ReligionID, ReligionName FROM curated_ReligionMaster ORDER BY ReligionID",
        "castes": "SELECT caste_master_id, caste_master_name FROM curated_CasteMaster ORDER BY caste_master_id",
        "courts": "SELECT CourtID, CourtName, DistrictID, StateID FROM curated_Court ORDER BY CourtName",
    }

    def __init__(self, store: DataStore) -> None:
        self._store = store
        self._cache: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.RLock()

    # -------------------------------------------------------------- caching
    def _cached(self, name: str) -> list[dict[str, Any]]:
        with self._lock:
            if name not in self._cache:
                self._cache[name] = self._store.query(self._CACHEABLE[name])
            return self._cache[name]

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    def warm(self) -> dict[str, int]:
        return {name: len(self._cached(name)) for name in self._CACHEABLE}

    # --------------------------------------------------------------- access
    def districts(self) -> list[dict[str, Any]]:
        return self._cached("districts")

    def units(self) -> list[dict[str, Any]]:
        return self._cached("units")

    def crime_heads(self) -> list[dict[str, Any]]:
        return self._cached("crime_heads")

    def crime_sub_heads(self) -> list[dict[str, Any]]:
        return self._cached("crime_sub_heads")

    def case_statuses(self) -> list[dict[str, Any]]:
        return self._cached("case_statuses")

    def case_categories(self) -> list[dict[str, Any]]:
        return self._cached("case_categories")

    def gravity_levels(self) -> list[dict[str, Any]]:
        return self._cached("gravity")

    def acts(self) -> list[dict[str, Any]]:
        return self._cached("acts")

    def occupations(self) -> list[dict[str, Any]]:
        return self._cached("occupations")

    def religions(self) -> list[dict[str, Any]]:
        return self._cached("religions")

    def castes(self) -> list[dict[str, Any]]:
        return self._cached("castes")

    def courts(self) -> list[dict[str, Any]]:
        return self._cached("courts")

    def unit(self, unit_id: int) -> dict[str, Any] | None:
        return next((u for u in self.units() if _same_id(u["UnitID"], unit_id)), None)

    def district(self, district_id: int) -> dict[str, Any] | None:
        return next((d for d in self.districts() if _same_id(d["DistrictID"], district_id)), None)

    def sections_for_act(self, act_code: str) -> list[dict[str, Any]]:
        return self._store.query(
            "SELECT ActCode, SectionCode, SectionDescription FROM curated_Section"
            " WHERE ActCode = :act ORDER BY SectionCode",
            {"act": act_code},
        )

    # ------------------------------------------------------------ hierarchy
    def rebuild_unit_closure(self) -> int:
        """Materialize the transitive closure of Unit.ParentUnit (§6.2)."""
        units = self._store.query("SELECT UnitID, ParentUnit FROM curated_Unit")
        parents = {int(u["UnitID"]): (int(u["ParentUnit"]) if u["ParentUnit"] is not None else None) for u in units}
        rows: list[dict[str, Any]] = []
        for unit_id in parents:
            depth = 0
            current: int | None = unit_id
            seen: set[int] = set()
            while current is not None and current not in seen:
                seen.add(current)
                rows.append({"a": current, "d": unit_id, "depth": depth})
                current = parents.get(current)
                depth += 1
        self._store.execute("DELETE FROM cip_unit_closure")
        self._store.execute_many(
            "INSERT OR REPLACE INTO cip_unit_closure (ancestor_unit_id, descendant_unit_id, depth)"
            " VALUES (:a, :d, :depth)",
            rows,
        )
        return len(rows)

    def descendant_unit_ids(self, unit_id: int) -> set[int]:
        rows = self._store.query(
            "SELECT descendant_unit_id FROM cip_unit_closure WHERE ancestor_unit_id = :u", {"u": unit_id}
        )
        return {int(row["descendant_unit_id"]) for row in rows} or {unit_id}

    def unit_ids_for_district(self, district_id: int) -> set[int]:
        # Compare as ints, not as-is: SQLite returns INTEGER columns as Python
        # ints, Catalyst returns them as strings ('2928'), and `'2928' == 2928`
        # is False -- so this silently returned an empty set on Catalyst and
        # every district-filtered query matched nothing.
        target = int(district_id)
        return {
            int(u["UnitID"])
            for u in self.units()
            if u.get("DistrictID") is not None and int(u["DistrictID"]) == target
        }

    # -------------------------------------------------------- name matching
    def resolve_district(self, name: str, *, threshold: float = 0.78) -> dict[str, Any] | None:
        return _best_match(name, self.districts(), "DistrictName", threshold)

    def resolve_unit(self, name: str, *, threshold: float = 0.72) -> dict[str, Any] | None:
        return _best_match(name, self.units(), "UnitName", threshold)

    def resolve_crime_sub_head(self, name: str, *, threshold: float = 0.7) -> dict[str, Any] | None:
        return _best_match(name, self.crime_sub_heads(), "CrimeHeadName", threshold)

    def resolve_case_status(self, name: str, *, threshold: float = 0.7) -> dict[str, Any] | None:
        return _best_match(name, self.case_statuses(), "CaseStatusName", threshold)


def _best_match(needle: str, rows: list[dict[str, Any]], field: str, threshold: float) -> dict[str, Any] | None:
    if not needle:
        return None
    target = needle.strip().casefold()
    best: tuple[float, dict[str, Any]] | None = None
    for row in rows:
        value = str(row.get(field) or "").casefold()
        if not value:
            continue
        if value == target:
            return row
        if target and (target in value or value in target):
            score = 0.92
        else:
            score = SequenceMatcher(None, target, value).ratio()
        if best is None or score > best[0]:
            best = (score, row)
    if best and best[0] >= threshold:
        return best[1]
    return None
