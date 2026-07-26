"""Seed orchestrator — one command from empty database to working platform.

Sequence:

    migrations → masters → cases → financial extension → NDJSON batches
    → load (raw + curated) → data quality → intelligence refresh
    → demo user accounts → manifest

The manifest is written to the file store and returned, and the tests assert
against it: if the generator plants three hotspots and a surge, the analytics
must find them again. That closed loop is the only honest way to validate
analytics on synthetic data.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ...domain.enums import DQSeverity, Role
from ...domain.ports import Clock, FileStore
from ...infrastructure.db.migrations import apply_migrations
from ...infrastructure.db.repositories import ControlRepository, UserRepository
from ...infrastructure.observability import get_logger
from ..services.identity import IdentityService
from .dq import DataQualitySuite
from .generators import CaseGenerator, generate_masters, generate_transactions
from .intelligence import IntelligenceRefresher
from .loader import BatchDescriptor, BatchWriter, Loader

LOGGER = get_logger(__name__)

DEMO_USERS = [
    ("io.bengaluru", "Investigating Officer, Bengaluru City", Role.INVESTIGATOR, "Bengaluru City"),
    ("analyst.state", "Crime Analyst, State CID", Role.ANALYST, None),
    ("sp.mysuru", "Superintendent of Police, Mysuru", Role.SUPERVISOR, "Mysuru"),
    ("policy.home", "Policy Adviser, Home Department", Role.POLICYMAKER, None),
    ("auditor.internal", "Internal Auditor", Role.AUDITOR, None),
    ("admin.platform", "Platform Administrator", Role.PLATFORM_ADMIN, None),
]
DEMO_PASSWORD = "ChangeMe#2026"


class SeedPipeline:
    def __init__(
        self,
        *,
        store: Any,
        filestore: FileStore,
        control: ControlRepository,
        users: UserRepository,
        identity_service: IdentityService,
        dq: DataQualitySuite,
        refresher: IntelligenceRefresher,
        reference: Any,
        clock: Clock,
        seed: int,
    ) -> None:
        self._store = store
        self._filestore = filestore
        self._control = control
        self._users = users
        self._identity_service = identity_service
        self._dq = dq
        self._refresher = refresher
        self._reference = reference
        self._clock = clock
        self._seed = seed

    def run(self, *, target_cases: int = 4200, months: int = 30, reset: bool = False) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        apply_migrations(self._store)
        if reset:
            self._truncate()

        rng = random.Random(self._seed)
        anchor = self._clock.now().date()

        LOGGER.info("seed_start", extra={"target_cases": target_cases, "anchor": anchor.isoformat()})
        masters = generate_masters(rng)
        generator = CaseGenerator(masters, rng, anchor=anchor, months=months)
        cases, manifest = generator.generate(target_cases)
        transactions = generate_transactions(cases, rng, anchor=anchor)

        writer = BatchWriter(self._filestore, self._control)
        loader = Loader(self._store, self._control, writer)
        batch_stamp = self._clock.now().strftime("%Y%m%dT%H%M%S")
        descriptors: list[BatchDescriptor] = []

        for table, rows in masters.tables().items():
            if rows:
                descriptors.append(writer.write(table, rows, batch_id=f"{batch_stamp}-{table}"))

        fact_tables: dict[str, list[dict[str, Any]]] = {
            "curated_CaseMaster": [c.case for c in cases],
            "curated_ComplainantDetails": [row for c in cases for row in c.complainants],
            "curated_Victim": [row for c in cases for row in c.victims],
            "curated_Accused": [row for c in cases for row in c.accused],
            "curated_ActSectionAssociation": [row for c in cases for row in c.act_sections],
            "curated_ArrestSurrender": [row for c in cases for row in c.arrests],
            "curated_ChargesheetDetails": [row for c in cases for row in c.chargesheets],
        }
        for table, rows in fact_tables.items():
            if rows:
                descriptors.append(writer.write(table, rows, batch_id=f"{batch_stamp}-{table}"))

        load_stats = loader.load(descriptors)
        self._write_transactions(transactions)
        self._reference.invalidate()

        findings = self._dq.run(batch_id=batch_stamp)
        blocking = [f for f in findings if not f.passed and f.severity == str(DQSeverity.BLOCKER)]
        if blocking:
            raise RuntimeError(
                "Data quality gate failed: " + ", ".join(f.check_name for f in blocking)
            )

        refresh = self._refresher.refresh_all(transactions=transactions)
        created_users = self._seed_users(masters)
        seeded_events = self._seed_events(anchor)

        summary = {
            "seed": self._seed,
            "anchor_date": anchor.isoformat(),
            "target_cases": target_cases,
            "generated_cases": len(cases),
            "transactions": len(transactions),
            "load": load_stats,
            "data_quality": {
                "checks": len(findings),
                "passed": sum(1 for f in findings if f.passed),
                "warnings": [f.check_name for f in findings if not f.passed],
            },
            "intelligence": refresh.as_dict(),
            "users": created_users,
            "events": seeded_events,
            "manifest": asdict(manifest),
            "duration_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 2),
        }
        self._filestore.write_bytes(
            "manifests/seed_manifest.json",
            json.dumps(summary, indent=2, default=str).encode("utf-8"),
            content_type="application/json",
        )
        self._control.set_watermark("seed", anchor.isoformat(), {"cases": len(cases)})
        LOGGER.info("seed_complete", extra={"cases": len(cases), "edges": refresh.edges})
        return summary

    # -------------------------------------------------------------- helpers
    def _write_transactions(self, transactions: list[dict[str, Any]]) -> None:
        if not transactions:
            return
        from ...infrastructure.db.repositories import FinancialRepository

        FinancialRepository(self._store).replace_all(transactions)

    def _seed_users(self, masters: Any) -> list[dict[str, Any]]:
        if self._users.count() > 0:
            return [{"username": row["username"], "role": row["role"], "created": False}
                    for row in self._users.list_all()]
        district_units = {
            row["DistrictName"]: [
                unit for unit in masters.units
                if unit["DistrictID"] == row["DistrictID"] and unit["TypeID"] == 6
            ]
            for row in masters.districts
        }
        created: list[dict[str, Any]] = []
        for username, display_name, role, district_name in DEMO_USERS:
            home_unit_id = None
            district_id = None
            if district_name and district_units.get(district_name):
                unit = district_units[district_name][0]
                home_unit_id = int(unit["UnitID"]) if role is Role.INVESTIGATOR else int(unit["ParentUnit"])
                district_id = int(unit["DistrictID"])
            self._identity_service.register(
                username=username,
                password=DEMO_PASSWORD,
                display_name=display_name,
                role=role,
                home_unit_id=home_unit_id,
                district_id=district_id,
            )
            created.append({"username": username, "role": str(role), "created": True,
                            "home_unit_id": home_unit_id})
        return created

    def _seed_events(self, anchor: date) -> int:
        """Seed a small set of clearly-labelled synthetic reference events.

        ``source`` and ``data_quality`` say plainly that these are synthetic;
        ``approval_status`` records that they are cleared for use inside this
        synthetic build. A real deployment replaces these rows through the
        governed ingestion path and does not inherit them.
        """
        from ...infrastructure.db.repositories import EventCalendarRepository

        events = EventCalendarRepository(self._store)
        if events.count() > 0:
            return 0

        # Anchored to the seeded window so a comparison has data on both sides.
        definitions = [
            ("dasara", "Dasara", "festival", 300, 9),
            ("deepavali", "Deepavali", "festival", 240, 4),
            ("year-end", "Year-end public gathering", "gathering", 180, 2),
        ]
        created_at = self._clock.now().isoformat()
        written = 0
        for slug, name, event_type, days_before, duration in definitions:
            start = anchor - timedelta(days=days_before)
            events.upsert({
                "event_id": f"synthetic-{slug}",
                "event_name": name,
                "event_type": event_type,
                "date_from": start.isoformat(),
                "date_to": (start + timedelta(days=duration)).isoformat(),
                "district_id": None,
                "unit_id": None,
                "source": "synthetic-demo",
                "data_quality": "synthetic",
                "approval_status": "approved",
                "created_at": created_at,
            })
            written += 1
        LOGGER.info("event_calendar_seeded", extra={"events": written})
        return written

    def _truncate(self) -> None:
        """Delete in strict child-before-parent order so foreign keys hold.

        Foreign key enforcement stays *on* during truncation deliberately: if
        this order is ever wrong, the reset fails loudly instead of leaving a
        half-cleared database that later produces silently wrong analytics.
        """
        tables = [
            # derived and platform-owned tables first — they point at everything
            "cip_case_priority", "cip_early_warning_alert", "cip_hotspot_cell",
            "cip_repeat_offender_score", "cip_entity_resolution_link", "cip_person_identity",
            "cip_embedding_index", "cip_embedding_stats", "cip_graph_edge",
            "ext_financial_transaction",
            # curated children
            "curated_ChargesheetDetails", "curated_ArrestSurrender",
            "curated_ActSectionAssociation", "curated_Accused", "curated_Victim",
            "curated_ComplainantDetails", "curated_CaseMaster",
            "curated_CrimeHeadActSection", "curated_Section", "curated_Act",
            "curated_CrimeSubHead", "curated_CrimeHead",
            "curated_Court", "curated_Employee", "cip_unit_closure", "curated_Unit",
            "curated_UnitType", "curated_District", "curated_State",
            # free-standing masters
            "curated_Rank", "curated_Designation", "curated_CaseCategory",
            "curated_GravityOffence", "curated_CaseStatusMaster", "curated_CasteMaster",
            "curated_ReligionMaster", "curated_OccupationMaster",
            # ingestion bookkeeping
            "raw_record", "ctl_row_hash", "ctl_dq_result", "ctl_batch_log", "ctl_job_watermark",
        ]
        with self._store.transaction():
            for table in tables:
                self._store.execute(f'DELETE FROM "{table}"', {})
        LOGGER.info("tables_truncated", extra={"tables": len(tables)})
