"""Key/value store backed by the relational store (Catalyst NoSQL stand-in).

Namespaces mirror the plan's NoSQL tables: ``conversation_memory``,
``agent_scratchpad``, ``embedding_cache``, ``session_state``. TTL semantics
are enforced on read *and* by a purge job, so an expired document is never
observable even if the purge has not run.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from ...domain.ports import DataStore


class RelationalKeyValueStore:
    def __init__(self, store: DataStore) -> None:
        self._store = store

    def put(self, namespace: str, key: str, value: Mapping[str, Any], ttl_seconds: int | None = None) -> None:
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat() if ttl_seconds else None
        self._store.execute(
            """
            INSERT INTO cip_kv (namespace, key, value_json, expires_at, updated_at)
            VALUES (:ns, :k, :v, :e, :u)
            ON CONFLICT (namespace, key) DO UPDATE SET
                value_json = excluded.value_json,
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            {"ns": namespace, "k": key, "v": json.dumps(dict(value), ensure_ascii=False, default=str),
             "e": expires, "u": now.isoformat()},
        )

    def get(self, namespace: str, key: str) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc).isoformat()
        rows = self._store.query(
            "SELECT value_json FROM cip_kv WHERE namespace = :ns AND key = :k"
            " AND (expires_at IS NULL OR expires_at > :now)",
            {"ns": namespace, "k": key, "now": now},
        )
        if not rows:
            return None
        return json.loads(rows[0]["value_json"])

    def delete(self, namespace: str, key: str) -> None:
        self._store.execute("DELETE FROM cip_kv WHERE namespace = :ns AND key = :k", {"ns": namespace, "k": key})

    def scan(self, namespace: str, key_prefix: str = "", limit: int = 500) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        rows = self._store.query(
            "SELECT key, value_json FROM cip_kv WHERE namespace = :ns AND key LIKE :prefix"
            " AND (expires_at IS NULL OR expires_at > :now) ORDER BY key LIMIT :limit",
            {"ns": namespace, "prefix": f"{key_prefix}%", "now": now, "limit": limit},
        )
        return [{"key": r["key"], **json.loads(r["value_json"])} for r in rows]

    def purge_expired(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        return self._store.execute(
            "DELETE FROM cip_kv WHERE expires_at IS NOT NULL AND expires_at <= :now", {"now": now}
        )
