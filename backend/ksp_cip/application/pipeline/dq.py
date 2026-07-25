"""Data quality checks run after every load (architecture §7).

Findings are recorded, never silently corrected, and severity decides whether
the pipeline halts. A `blocking` failure means the curated layer cannot be
trusted for the affected table; `warning` findings are surfaced in the admin
view and in the platform's own honesty about coverage.

The checks are deliberately schema-specific rather than generic: a CrimeNo that
does not parse, a case whose registration precedes its incident, or a foreign
key that points nowhere are the failures that would actually mislead an officer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ...domain.enums import DQSeverity
from ...domain.ports import DataStore
from ...domain.value_objects import CrimeNo, KARNATAKA_BBOX
from ...infrastructure.db.repositories import ControlRepository
from ...infrastructure.observability import get_logger

LOGGER = get_logger(__name__)


@dataclass(slots=True)
class DQFinding:
    check_name: str
    table_name: str
    severity: str
    passed: bool
    observed: int
    threshold: int
    detail: dict[str, Any]
    total: int = 0


class DataQualitySuite:
    def __init__(self, store: DataStore, control: ControlRepository) -> None:
        self._store = store
        self._control = control

    def run(self, *, batch_id: str) -> list[DQFinding]:
        findings: list[DQFinding] = []
        for check in (
            self._crime_no_format,
            self._crime_no_unique,
            self._case_dates_ordered,
            self._coordinates_in_state,
            self._orphan_children,
            self._missing_classification,
            self._future_dates,
            self._accused_name_present,
        ):
            findings.extend(check())

        self._control.record_dq([
            {
                "batch_id": batch_id,
                "source_table": finding.table_name,
                "check_name": finding.check_name,
                "severity": finding.severity,
                "passed": finding.passed,
                "failed_rows": finding.observed,
                "total_rows": finding.total,
                "detail": {**finding.detail, "threshold": finding.threshold},
            }
            for finding in findings
        ])
        blocking = [f for f in findings if not f.passed and f.severity == str(DQSeverity.BLOCKER)]
        if blocking:
            LOGGER.error("dq_blocking", extra={"checks": [f.check_name for f in blocking]})
        return findings

    # --------------------------------------------------------------- checks
    def _count(self, sql: str, params: dict[str, Any] | None = None) -> int:
        rows = self._store.query(sql, params or {})
        return int(rows[0]["n"]) if rows else 0

    def _crime_no_format(self) -> list[DQFinding]:
        rows = self._store.query("SELECT CaseMasterID, CrimeNo FROM curated_CaseMaster", {})
        bad = [row["CaseMasterID"] for row in rows if CrimeNo.try_parse(str(row["CrimeNo"])) is None]
        return [DQFinding(
            check_name="crime_no_format", table_name="curated_CaseMaster",
            severity=str(DQSeverity.BLOCKER), passed=not bad, observed=len(bad), threshold=0,
            detail={"sample_case_ids": bad[:10],
                    "rule": "18 digits: 1 category + 4 district + 4 station + 4 year + 5 serial"},
        )]

    def _crime_no_unique(self) -> list[DQFinding]:
        duplicates = self._count(
            "SELECT COUNT(*) AS n FROM (SELECT CrimeNo FROM curated_CaseMaster "
            "GROUP BY CrimeNo HAVING COUNT(*) > 1)"
        )
        return [DQFinding(
            check_name="crime_no_unique", table_name="curated_CaseMaster",
            severity=str(DQSeverity.BLOCKER), passed=duplicates == 0, observed=duplicates, threshold=0,
            detail={"rule": "CrimeNo is unique per police station, category and year"},
        )]

    def _case_dates_ordered(self) -> list[DQFinding]:
        bad = self._count(
            "SELECT COUNT(*) AS n FROM curated_CaseMaster "
            "WHERE IncidentFromDate IS NOT NULL AND CrimeRegisteredDate IS NOT NULL "
            "AND date(substr(IncidentFromDate, 1, 10)) > date(CrimeRegisteredDate)"
        )
        return [DQFinding(
            check_name="incident_before_registration", table_name="curated_CaseMaster",
            severity=str(DQSeverity.WARN), passed=bad == 0, observed=bad, threshold=0,
            detail={"rule": "IncidentFromDate must not fall after CrimeRegisteredDate"},
        )]

    def _coordinates_in_state(self) -> list[DQFinding]:
        lat_min, lat_max, lon_min, lon_max = KARNATAKA_BBOX
        outside = self._count(
            "SELECT COUNT(*) AS n FROM curated_CaseMaster WHERE latitude IS NOT NULL "
            "AND (latitude < :lat_min OR latitude > :lat_max OR longitude < :lon_min OR longitude > :lon_max)",
            {"lat_min": lat_min, "lat_max": lat_max, "lon_min": lon_min, "lon_max": lon_max},
        )
        total = self._count("SELECT COUNT(*) AS n FROM curated_CaseMaster WHERE latitude IS NOT NULL")
        threshold = max(1, int(total * 0.02))
        return [DQFinding(
            check_name="coordinates_within_karnataka", table_name="curated_CaseMaster",
            severity=str(DQSeverity.WARN), passed=outside <= threshold, observed=outside,
            threshold=threshold,
            detail={"bbox": list(KARNATAKA_BBOX), "geocoded_rows": total},
        )]

    def _orphan_children(self) -> list[DQFinding]:
        findings: list[DQFinding] = []
        for table in ("curated_Victim", "curated_Accused", "curated_ComplainantDetails",
                      "curated_ArrestSurrender", "curated_ChargesheetDetails",
                      "curated_ActSectionAssociation"):
            orphans = self._count(
                f'SELECT COUNT(*) AS n FROM "{table}" child '
                "LEFT JOIN curated_CaseMaster parent ON parent.CaseMasterID = child.CaseMasterID "
                "WHERE parent.CaseMasterID IS NULL"
            )
            findings.append(DQFinding(
                check_name="orphan_case_reference", table_name=table,
                severity=str(DQSeverity.BLOCKER), passed=orphans == 0, observed=orphans, threshold=0,
                detail={"rule": "every child row must reference an existing CaseMasterID"},
            ))
        return findings

    def _missing_classification(self) -> list[DQFinding]:
        missing = self._count(
            "SELECT COUNT(*) AS n FROM curated_CaseMaster WHERE CrimeMinorHeadID IS NULL"
        )
        total = self._count("SELECT COUNT(*) AS n FROM curated_CaseMaster")
        threshold = max(1, int(total * 0.05))
        return [DQFinding(
            check_name="crime_classification_present", table_name="curated_CaseMaster",
            severity=str(DQSeverity.WARN), passed=missing <= threshold, observed=missing,
            threshold=threshold,
            detail={"rule": "cases without a crime sub-head are excluded from crime-type analytics"},
        )]

    def _future_dates(self) -> list[DQFinding]:
        future = self._count(
            "SELECT COUNT(*) AS n FROM curated_CaseMaster "
            "WHERE date(CrimeRegisteredDate) > date('now', '+1 day')"
        )
        return [DQFinding(
            check_name="registration_not_in_future", table_name="curated_CaseMaster",
            severity=str(DQSeverity.WARN), passed=future == 0, observed=future, threshold=0,
            detail={"rule": "CrimeRegisteredDate must not be in the future"},
        )]

    def _accused_name_present(self) -> list[DQFinding]:
        blank = self._count(
            "SELECT COUNT(*) AS n FROM curated_Accused WHERE AccusedName IS NULL OR trim(AccusedName) = ''"
        )
        return [DQFinding(
            check_name="accused_name_present", table_name="curated_Accused",
            severity=str(DQSeverity.WARN), passed=blank == 0, observed=blank, threshold=0,
            detail={"rule": "entity resolution silently drops rows with no name"},
        )]
