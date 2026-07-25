"""Platform-plane repositories: accounts, conversation memory, audit, control."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from ....domain.ports import DataStore
from .cases import in_clause


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class UserRepository:
    def __init__(self, store: DataStore) -> None:
        self._store = store

    def create(self, record: Mapping[str, Any]) -> None:
        self._store.execute(
            "INSERT INTO cip_user_account (user_id, username, display_name, role, home_unit_id, district_id,"
            " password_salt, password_hash, active, created_at)"
            " VALUES (:user_id, :username, :display_name, :role, :home_unit_id, :district_id,"
            " :password_salt, :password_hash, 1, :created_at)"
            " ON CONFLICT (user_id) DO UPDATE SET display_name = excluded.display_name,"
            " role = excluded.role, home_unit_id = excluded.home_unit_id,"
            " district_id = excluded.district_id, password_salt = excluded.password_salt,"
            " password_hash = excluded.password_hash",
            {**record, "created_at": _now()},
        )

    def by_username(self, username: str) -> dict[str, Any] | None:
        rows = self._store.query(
            "SELECT * FROM cip_user_account WHERE username = :u AND active = 1", {"u": username}
        )
        return rows[0] if rows else None

    def by_id(self, user_id: str) -> dict[str, Any] | None:
        rows = self._store.query(
            "SELECT * FROM cip_user_account WHERE user_id = :i AND active = 1", {"i": user_id}
        )
        return rows[0] if rows else None

    def list_all(self) -> list[dict[str, Any]]:
        return self._store.query(
            "SELECT user_id, username, display_name, role, home_unit_id, district_id, active"
            " FROM cip_user_account ORDER BY username"
        )

    def count(self) -> int:
        rows = self._store.query("SELECT COUNT(*) AS n FROM cip_user_account")
        return int(rows[0]["n"]) if rows else 0


class ConversationRepository:
    """Conversation memory. TTL enforced on read and by the purge job."""

    def __init__(self, store: DataStore, *, ttl_days: int = 30) -> None:
        self._store = store
        self._ttl_days = ttl_days

    def append(self, turn: Mapping[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        self._store.execute(
            "INSERT OR REPLACE INTO cip_conversation_turn (session_id, turn_seq, user_id, role, created_at,"
            " expires_at, user_text_original, user_text_english, user_language, answer_text_english,"
            " answer_text_display, intent, evidence_json, pinned_json, payload_type)"
            " VALUES (:session_id, :turn_seq, :user_id, :role, :created_at, :expires_at,"
            " :user_text_original, :user_text_english, :user_language, :answer_text_english,"
            " :answer_text_display, :intent, :evidence_json, :pinned_json, :payload_type)",
            {
                "session_id": turn["session_id"],
                "turn_seq": turn["turn_seq"],
                "user_id": turn["user_id"],
                "role": turn["role"],
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=self._ttl_days)).isoformat(),
                "user_text_original": turn["user_text_original"],
                "user_text_english": turn["user_text_english"],
                "user_language": turn["user_language"],
                "answer_text_english": turn["answer_text_english"],
                "answer_text_display": turn["answer_text_display"],
                "intent": turn["intent"],
                "evidence_json": json.dumps(turn.get("evidence_locators", []), ensure_ascii=False),
                "pinned_json": json.dumps(turn.get("pinned", {}), ensure_ascii=False),
                "payload_type": turn.get("payload_type", "none"),
            },
        )

    def recent_turns(self, session_id: str, limit: int = 8) -> list[dict[str, Any]]:
        rows = self._store.query(
            "SELECT * FROM cip_conversation_turn WHERE session_id = :s AND expires_at > :now"
            " ORDER BY turn_seq DESC LIMIT :limit",
            {"s": session_id, "now": _now(), "limit": limit},
        )
        return [_decode_turn(row) for row in reversed(rows)]

    def all_turns(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._store.query(
            "SELECT * FROM cip_conversation_turn WHERE session_id = :s AND expires_at > :now"
            " ORDER BY turn_seq ASC",
            {"s": session_id, "now": _now()},
        )
        return [_decode_turn(row) for row in rows]

    def next_turn_seq(self, session_id: str) -> int:
        rows = self._store.query(
            "SELECT COALESCE(MAX(turn_seq), 0) AS m FROM cip_conversation_turn WHERE session_id = :s",
            {"s": session_id},
        )
        return int(rows[0]["m"]) + 1 if rows else 1

    def sessions_for_user(self, user_id: str, limit: int = 25) -> list[dict[str, Any]]:
        return self._store.query(
            "SELECT session_id, MIN(created_at) AS started_at, MAX(created_at) AS last_at,"
            " COUNT(*) AS turn_count FROM cip_conversation_turn"
            " WHERE user_id = :u AND expires_at > :now GROUP BY session_id"
            " ORDER BY last_at DESC LIMIT :limit",
            {"u": user_id, "now": _now(), "limit": limit},
        )

    def purge_expired(self) -> int:
        return self._store.execute(
            "DELETE FROM cip_conversation_turn WHERE expires_at <= :now", {"now": _now()}
        )


def _decode_turn(row: dict[str, Any]) -> dict[str, Any]:
    decoded = dict(row)
    decoded["evidence_locators"] = json.loads(row.get("evidence_json") or "[]")
    decoded["pinned"] = json.loads(row.get("pinned_json") or "{}")
    return decoded


class AuditRepository:
    """Append-only audit sink (architecture §12.3). No update or delete path."""

    def __init__(self, store: DataStore) -> None:
        self._store = store

    def record(self, event: Mapping[str, Any]) -> None:
        self._store.execute(
            "INSERT INTO audit_event (event_id, occurred_at, actor_user_id, actor_role, scope_summary,"
            " purpose_code, action, agent, object_type, object_ids, request_hash, outcome, latency_ms,"
            " correlation_id, detail_json)"
            " VALUES (:event_id, :occurred_at, :actor_user_id, :actor_role, :scope_summary, :purpose_code,"
            " :action, :agent, :object_type, :object_ids, :request_hash, :outcome, :latency_ms,"
            " :correlation_id, :detail_json)",
            {
                "event_id": event.get("event_id") or uuid.uuid4().hex,
                "occurred_at": event.get("occurred_at") or _now(),
                "actor_user_id": event.get("actor_user_id"),
                "actor_role": event.get("actor_role"),
                "scope_summary": event.get("scope_summary"),
                "purpose_code": event.get("purpose_code", "unspecified"),
                "action": event["action"],
                "agent": event.get("agent"),
                "object_type": event.get("object_type"),
                "object_ids": json.dumps(event.get("object_ids", []), ensure_ascii=False),
                "request_hash": event.get("request_hash"),
                "outcome": event.get("outcome", "success"),
                "latency_ms": event.get("latency_ms"),
                "correlation_id": event.get("correlation_id"),
                "detail_json": json.dumps(event.get("detail", {}), ensure_ascii=False, default=str),
            },
        )

    def query(self, filters: Mapping[str, Any], limit: int = 200) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit}
        if filters.get("actor_user_id"):
            clauses.append("actor_user_id = :actor_user_id")
            params["actor_user_id"] = filters["actor_user_id"]
        if filters.get("action"):
            clauses.append("action = :action")
            params["action"] = filters["action"]
        if filters.get("since"):
            clauses.append("occurred_at >= :since")
            params["since"] = filters["since"]
        if filters.get("correlation_id"):
            clauses.append("correlation_id = :correlation_id")
            params["correlation_id"] = filters["correlation_id"]
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._store.query(
            f"SELECT * FROM audit_event{where} ORDER BY occurred_at DESC LIMIT :limit", params
        )
        for row in rows:
            row["object_ids"] = json.loads(row.get("object_ids") or "[]")
            row["detail"] = json.loads(row.pop("detail_json") or "{}")
        return rows

    def stats(self) -> dict[str, Any]:
        rows = self._store.query(
            "SELECT action, outcome, COUNT(*) AS n FROM audit_event GROUP BY action, outcome ORDER BY n DESC"
        )
        total = sum(int(r["n"]) for r in rows)
        return {"total": total, "by_action": rows}


class ControlRepository:
    """Batch log, watermarks and data-quality results (architecture §5.5/§5.7)."""

    def __init__(self, store: DataStore) -> None:
        self._store = store

    def register_batch(self, batch: Mapping[str, Any]) -> None:
        self._store.execute(
            "INSERT INTO ctl_batch_log (batch_id, source_table, row_count, min_pk, max_pk, content_sha256,"
            " status, object_key, received_at)"
            " VALUES (:batch_id, :source_table, :row_count, :min_pk, :max_pk, :content_sha256, :status,"
            " :object_key, :received_at)"
            " ON CONFLICT (batch_id) DO NOTHING",
            {**batch, "received_at": batch.get("received_at") or _now()},
        )

    def mark_batch(self, batch_id: str, status: str, error: str | None = None) -> None:
        self._store.execute(
            "UPDATE ctl_batch_log SET status = :s, loaded_at = :t, error_detail = :e WHERE batch_id = :b",
            {"s": status, "t": _now(), "e": error, "b": batch_id},
        )

    def batch(self, batch_id: str) -> dict[str, Any] | None:
        rows = self._store.query("SELECT * FROM ctl_batch_log WHERE batch_id = :b", {"b": batch_id})
        return rows[0] if rows else None

    def batches(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._store.query(
            "SELECT * FROM ctl_batch_log ORDER BY received_at DESC LIMIT :limit", {"limit": limit}
        )

    def set_watermark(self, job: str, value: str, detail: Mapping[str, Any] | None = None) -> None:
        self._store.execute(
            "INSERT INTO ctl_job_watermark (job_name, watermark_value, updated_at, detail_json)"
            " VALUES (:j, :v, :t, :d)"
            " ON CONFLICT (job_name) DO UPDATE SET watermark_value = excluded.watermark_value,"
            " updated_at = excluded.updated_at, detail_json = excluded.detail_json",
            {"j": job, "v": value, "t": _now(), "d": json.dumps(dict(detail or {}), default=str)},
        )

    def watermark(self, job: str) -> dict[str, Any] | None:
        rows = self._store.query("SELECT * FROM ctl_job_watermark WHERE job_name = :j", {"j": job})
        if not rows:
            return None
        row = rows[0]
        row["detail"] = json.loads(row.pop("detail_json") or "{}")
        return row

    def watermarks(self) -> list[dict[str, Any]]:
        rows = self._store.query("SELECT * FROM ctl_job_watermark ORDER BY job_name")
        for row in rows:
            row["detail"] = json.loads(row.pop("detail_json") or "{}")
        return rows

    def record_dq(self, results: Sequence[Mapping[str, Any]]) -> int:
        if not results:
            return 0
        rows = [
            {
                "batch_id": item["batch_id"],
                "source_table": item["source_table"],
                "check_name": item["check_name"],
                "severity": item["severity"],
                "passed": 1 if item["passed"] else 0,
                "failed_rows": item.get("failed_rows", 0),
                "total_rows": item.get("total_rows", 0),
                "detail_json": json.dumps(item.get("detail", {}), ensure_ascii=False, default=str),
                "checked_at": _now(),
            }
            for item in results
        ]
        return self._store.execute_many(
            "INSERT INTO ctl_dq_result (batch_id, source_table, check_name, severity, passed, failed_rows,"
            " total_rows, detail_json, checked_at)"
            " VALUES (:batch_id, :source_table, :check_name, :severity, :passed, :failed_rows, :total_rows,"
            " :detail_json, :checked_at)",
            rows,
        )

    def dq_results(self, limit: int = 200, batch_id: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        where = ""
        if batch_id:
            where = " WHERE batch_id = :b"
            params["b"] = batch_id
        rows = self._store.query(
            f"SELECT * FROM ctl_dq_result{where} ORDER BY checked_at DESC LIMIT :limit", params
        )
        for row in rows:
            row["detail"] = json.loads(row.pop("detail_json") or "{}")
        return rows

    def dq_summary(self) -> dict[str, Any]:
        rows = self._store.query(
            "SELECT severity, passed, COUNT(*) AS n, SUM(failed_rows) AS failed_rows"
            " FROM ctl_dq_result GROUP BY severity, passed"
        )
        return {"buckets": rows}

    # ------------------------------------------------------------ hash ledger
    def upsert_hashes(self, table: str, pairs: Sequence[tuple[str, str]]) -> int:
        if not pairs:
            return 0
        rows = [{"t": table, "pk": pk, "h": row_hash, "u": _now()} for pk, row_hash in pairs]
        return self._store.execute_many(
            "INSERT INTO ctl_row_hash (source_table, source_pk, row_hash, updated_at)"
            " VALUES (:t, :pk, :h, :u)"
            " ON CONFLICT (source_table, source_pk) DO UPDATE SET row_hash = excluded.row_hash,"
            " updated_at = excluded.updated_at",
            rows,
        )

    def changed_pks(self, table: str, pairs: Sequence[tuple[str, str]]) -> list[str]:
        """Hash-diff: return PKs whose incoming hash differs from the ledger."""
        if not pairs:
            return []
        incoming = dict(pairs)
        fragment, params = in_clause("pk", list(incoming))
        params["t"] = table
        existing = {
            row["source_pk"]: row["row_hash"]
            for row in self._store.query(
                f"SELECT source_pk, row_hash FROM ctl_row_hash WHERE source_table = :t"
                f" AND source_pk IN ({fragment})",
                params,
            )
        }
        return [pk for pk, row_hash in incoming.items() if existing.get(pk) != row_hash]

    def store_row_count(self, table: str) -> int:
        rows = self._store.query(f"SELECT COUNT(*) AS n FROM {_safe_table(table)}")  # noqa: S608
        return int(rows[0]["n"]) if rows else 0


_ALLOWED_TABLES = {
    "curated_CaseMaster", "curated_Accused", "curated_Victim", "curated_ComplainantDetails",
    "curated_ArrestSurrender", "curated_ChargesheetDetails", "curated_ActSectionAssociation",
    "curated_Unit", "curated_District", "curated_Employee", "curated_Act", "curated_Section",
    "curated_CrimeHead", "curated_CrimeSubHead", "curated_Court", "curated_State",
    "curated_UnitType", "curated_Rank", "curated_Designation", "curated_CaseCategory",
    "curated_GravityOffence", "curated_CaseStatusMaster", "curated_CasteMaster",
    "curated_ReligionMaster", "curated_OccupationMaster", "curated_CrimeHeadActSection",
    "cip_graph_edge", "cip_embedding_index", "ext_financial_transaction", "raw_record",
    # Derived intelligence tables, countable by the admin status view.
    "cip_person_identity", "cip_entity_resolution_link", "cip_repeat_offender_score",
    "cip_hotspot_cell", "cip_early_warning_alert", "cip_case_priority", "cip_unit_closure",
    "cip_conversation_turn", "cip_user_account", "audit_event",
}


def _safe_table(table: str) -> str:
    """Whitelist guard: table names are never interpolated from user input."""
    if table not in _ALLOWED_TABLES:
        from ....domain.errors import ValidationError

        raise ValidationError("Unknown table", table=table)
    return table
