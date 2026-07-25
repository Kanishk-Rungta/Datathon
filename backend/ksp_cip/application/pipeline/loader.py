"""Ingestion: NDJSON batches → raw → curated.

This mirrors the production ingestion contract (architecture §6) at hackathon
scale. The Sync Agent is absent, so the generator plays its role: it writes
gzip-free NDJSON batch files into the file store using the same layout the real
agent would (``landing/<table>/<batch_id>.ndjson``), and the loader consumes
them exactly as it would consume real extracts.

Keeping this shape has a practical payoff: swapping the generator for a real
Sync Agent later is a change of *producer*, not a change of pipeline.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from ...domain.errors import CIPError
from ...domain.ports import DataStore, FileStore
from ...infrastructure.db.repositories import ControlRepository
from ...infrastructure.observability import get_logger

LOGGER = get_logger(__name__)

#: Load order matters: parents before children, so foreign keys resolve.
CURATED_LOAD_ORDER = [
    "curated_State",
    "curated_District",
    "curated_UnitType",
    "curated_Unit",
    "curated_Rank",
    "curated_Designation",
    "curated_Employee",
    "curated_Court",
    "curated_CaseCategory",
    "curated_GravityOffence",
    "curated_CaseStatusMaster",
    "curated_ReligionMaster",
    "curated_CasteMaster",
    "curated_OccupationMaster",
    "curated_CrimeHead",
    "curated_CrimeSubHead",
    "curated_Act",
    "curated_Section",
    "curated_CrimeHeadActSection",
    "curated_CaseMaster",
    "curated_ComplainantDetails",
    "curated_Victim",
    "curated_Accused",
    "curated_ActSectionAssociation",
    "curated_ArrestSurrender",
    "curated_ChargesheetDetails",
]

PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "curated_State": ("StateID",),
    "curated_District": ("DistrictID",),
    "curated_UnitType": ("UnitTypeID",),
    "curated_Unit": ("UnitID",),
    "curated_Rank": ("RankID",),
    "curated_Designation": ("DesignationID",),
    "curated_Employee": ("EmployeeID",),
    "curated_Court": ("CourtID",),
    "curated_CaseCategory": ("CaseCategoryID",),
    "curated_GravityOffence": ("GravityOffenceID",),
    "curated_CaseStatusMaster": ("CaseStatusID",),
    "curated_ReligionMaster": ("ReligionID",),
    "curated_CasteMaster": ("caste_master_id",),
    "curated_OccupationMaster": ("OccupationID",),
    "curated_CrimeHead": ("CrimeHeadID",),
    "curated_CrimeSubHead": ("CrimeSubHeadID",),
    "curated_Act": ("ActCode",),
    "curated_Section": ("ActCode", "SectionCode"),
    "curated_CrimeHeadActSection": ("CrimeHeadID", "ActCode", "SectionCode"),
    "curated_CaseMaster": ("CaseMasterID",),
    "curated_ComplainantDetails": ("ComplainantID",),
    "curated_Victim": ("VictimMasterID",),
    "curated_Accused": ("AccusedMasterID",),
    "curated_ActSectionAssociation": ("CaseMasterID", "ActID", "SectionID"),
    "curated_ArrestSurrender": ("ArrestSurrenderID",),
    "curated_ChargesheetDetails": ("CSID",),
}

#: Columns the generator carries for its own use that are not part of the
#: organiser's schema. They are dropped at the curated boundary.
#: Columns a producer may carry for its own purposes. The conform step drops
#: anything the target table does not declare, which is what keeps the
#: organiser's schema authoritative: a producer cannot widen it by accident.
PRODUCER_ONLY_PREFIXES = ("cip_", "_")


def row_hash(row: Mapping[str, Any]) -> str:
    payload = json.dumps({k: v for k, v in sorted(row.items())}, sort_keys=True, default=str)
    return hashlib.blake2s(payload.encode("utf-8"), digest_size=16).hexdigest()


def primary_key_value(table: str, row: Mapping[str, Any]) -> str:
    keys = PRIMARY_KEYS[table]
    return "|".join(str(row.get(key)) for key in keys)


@dataclass(slots=True)
class BatchDescriptor:
    batch_id: str
    table: str
    key: str
    row_count: int


class BatchWriter:
    """Writes NDJSON batches into the file store, as the Sync Agent would."""

    def __init__(self, filestore: FileStore, control: ControlRepository) -> None:
        self._filestore = filestore
        self._control = control

    def write(self, table: str, rows: Sequence[Mapping[str, Any]], *, batch_id: str) -> BatchDescriptor:
        key = f"landing/{table}/{batch_id}.ndjson"
        body = "\n".join(json.dumps(dict(row), default=str, ensure_ascii=False) for row in rows)
        payload = body.encode("utf-8")
        self._filestore.write_bytes(key, payload, content_type="application/x-ndjson")
        primary_keys = sorted(primary_key_value(table, row) for row in rows) if rows else []
        self._control.register_batch({
            "batch_id": batch_id,
            "source_table": table,
            "object_key": key,
            "row_count": len(rows),
            "min_pk": primary_keys[0] if primary_keys else None,
            "max_pk": primary_keys[-1] if primary_keys else None,
            "content_sha256": hashlib.sha256(payload).hexdigest(),
            "status": "landed",
            "received_at": datetime.now(timezone.utc).isoformat(),
        })
        LOGGER.info("batch_written", extra={"table": table, "rows": len(rows), "key": key})
        return BatchDescriptor(batch_id=batch_id, table=table, key=key, row_count=len(rows))

    def read(self, descriptor: BatchDescriptor) -> list[dict[str, Any]]:
        if not self._filestore.exists(descriptor.key):
            return []
        payload = self._filestore.read_bytes(descriptor.key)
        return [json.loads(line) for line in payload.decode("utf-8").splitlines() if line.strip()]


class Loader:
    """Bronze → silver → gold, with hash-based change detection."""

    def __init__(self, store: DataStore, control: ControlRepository, writer: BatchWriter) -> None:
        self._store = store
        self._control = control
        self._writer = writer
        self._columns: dict[str, list[str]] = {}
        self._reported_drops: set[str] = set()

    def _table_columns(self, table: str) -> list[str]:
        """Columns the target table actually declares, read from the database.

        The curated schema is frozen and authoritative. Conforming against it at
        load time — rather than trusting the producer — means a generator or a
        future Sync Agent cannot silently add a column, and a mismatch surfaces
        as a logged drop instead of a schema change.
        """
        if table not in self._columns:
            rows = self._store.query(f'PRAGMA table_info("{table}")', {})
            self._columns[table] = [str(row["name"]) for row in rows]
        return self._columns[table]

    def _conform(self, table: str, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        declared = set(self._table_columns(table))
        if not declared:
            raise CIPError(f"Unknown curated table: {table}", table=table)
        dropped = {key for row in rows[:1] for key in row if key not in declared}
        if dropped and table not in self._reported_drops:
            self._reported_drops.add(table)
            LOGGER.info("producer_columns_dropped", extra={
                "table": table, "columns": sorted(dropped),
                "reason": "not declared by the curated schema",
            })
        return [{key: value for key, value in row.items() if key in declared} for row in rows]

    def load(self, descriptors: Sequence[BatchDescriptor]) -> dict[str, Any]:
        by_table: dict[str, list[BatchDescriptor]] = {}
        for descriptor in descriptors:
            by_table.setdefault(descriptor.table, []).append(descriptor)

        stats: dict[str, Any] = {"tables": {}, "raw_rows": 0, "curated_rows": 0, "unchanged": 0}
        for table in CURATED_LOAD_ORDER:
            for descriptor in by_table.get(table, []):
                rows = self._writer.read(descriptor)
                raw_written = self._write_raw(table, rows, descriptor.batch_id)
                curated_written, unchanged = self._write_curated(table, rows)
                self._control.mark_batch(descriptor.batch_id, "loaded")
                entry = stats["tables"].setdefault(table, {"raw": 0, "curated": 0, "unchanged": 0})
                entry["raw"] += raw_written
                entry["curated"] += curated_written
                entry["unchanged"] += unchanged
                stats["raw_rows"] += raw_written
                stats["curated_rows"] += curated_written
                stats["unchanged"] += unchanged
        return stats

    def _write_raw(self, table: str, rows: Sequence[Mapping[str, Any]], batch_id: str) -> int:
        extracted = datetime.now(timezone.utc).isoformat()
        payloads = [
            {
                "source_table": table,
                "source_pk": primary_key_value(table, row),
                "payload_json": json.dumps(dict(row), default=str, ensure_ascii=False),
                "batch_id": batch_id,
                "extracted_at": extracted,
                "row_hash": row_hash(row),
            }
            for row in rows
        ]
        if not payloads:
            return 0
        self._store.execute_many(
            """
            INSERT INTO raw_record (source_table, source_pk, payload_json, _batch_id,
                                    _extracted_at, _row_hash)
            VALUES (:source_table, :source_pk, :payload_json, :batch_id, :extracted_at, :row_hash)
            ON CONFLICT (source_table, source_pk, _batch_id) DO UPDATE SET
                payload_json  = excluded.payload_json,
                _row_hash     = excluded._row_hash,
                _extracted_at = excluded._extracted_at
            """,
            payloads,
        )
        return len(payloads)

    def _write_curated(self, table: str, rows: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
        if not rows:
            return 0, 0
        cleaned = self._conform(table, rows)
        pairs = [(primary_key_value(table, row), row_hash(row)) for row in cleaned]
        changed = set(self._control.changed_pks(table, pairs))
        to_write = [
            row for row, (pk, _hash) in zip(cleaned, pairs) if pk in changed
        ]
        unchanged = len(cleaned) - len(to_write)
        if to_write:
            columns = list(to_write[0].keys())
            placeholders = ", ".join(f":{column}" for column in columns)
            column_list = ", ".join(f'"{column}"' for column in columns)
            conflict = ", ".join(f'"{key}"' for key in PRIMARY_KEYS[table])
            updates = ", ".join(
                f'"{column}" = excluded."{column}"' for column in columns
                if column not in PRIMARY_KEYS[table]
            )
            sql = (
                f'INSERT INTO "{table}" ({column_list}) VALUES ({placeholders}) '
                f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}"
                if updates else
                f'INSERT INTO "{table}" ({column_list}) VALUES ({placeholders}) '
                f"ON CONFLICT ({conflict}) DO NOTHING"
            )
            # Rows within one batch must share a column set; the generator
            # guarantees this, and a mismatch is a bug worth failing on.
            self._store.execute_many(sql, [
                {column: row.get(column) for column in columns} for row in to_write
            ])
            self._control.upsert_hashes(table, [
                (primary_key_value(table, row), row_hash(row)) for row in to_write
            ])
        return len(to_write), unchanged
