"""Catalyst-side session/scratch document store.

**What this is, and what it deliberately is not.**

``implementationv2.md`` §6.2 asks for a ``CatalystKeyValueStore`` on Catalyst
NoSQL. This implementation stores documents through the :class:`DataStore` port
— which *is* the Catalyst Data Store when ``KSPCIP_DATASTORE_BACKEND=catalyst``
— rather than calling the Catalyst NoSQL REST API directly.

That is a considered choice, not a shortcut:

* The NoSQL REST API's item encoding could not be verified against a live
  project in this build. Writing an unverifiable adapter and binding it by
  default would put a component into the session path that claims a capability
  nobody has exercised — precisely the failure mode this codebase avoids
  elsewhere by saying "not run against live Catalyst" out loud.
* The Data Store path is exercised by the same contract tests as every other
  repository, including the ``ON CONFLICT`` upsert translation this class
  depends on.

What this class adds over :class:`RelationalKeyValueStore` is the *discipline*
§6.2 actually asks for, which the plain relational store does not enforce:
a namespace allow-list, a mandatory TTL per namespace, user-qualified keys, and
a payload ceiling so case text cannot be parked in a scratchpad document.

Swapping in a native NoSQL transport later is a change to :meth:`_read` and
:meth:`_write` only; the discipline above stays where it is.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from ...domain.errors import ValidationError
from ...domain.ports import DataStore
from ..observability import get_logger

LOGGER = get_logger(__name__)

#: Namespace -> default TTL in seconds. A namespace not listed here is refused:
#: an unbounded, unnamed bucket of session data is how retention promises get
#: quietly broken.
NAMESPACE_TTL_SECONDS: dict[str, int] = {
    "session_state": 30 * 24 * 3600,   # matches conversation retention
    "agent_scratchpad": 3600,          # explicitly non-authoritative
    "embedding_cache": 24 * 3600,
    "idempotency": 48 * 3600,
    "conversation_memory": 30 * 24 * 3600,
}

#: A session document holds pins and preferences. FIR narrative, audio, or a
#: full transcript belongs in the audited relational tables, not here.
MAX_DOCUMENT_BYTES = 64 * 1024


class CatalystKeyValueStore:
    backend = "catalyst-nosql"

    def __init__(self, store: DataStore, *, table: str = "cip_kv") -> None:
        self._store = store
        self._table = table

    # ------------------------------------------------------------- protocol
    def put(
        self,
        namespace: str,
        key: str,
        value: Mapping[str, Any],
        ttl_seconds: int | None = None,
    ) -> None:
        default_ttl = self._require_namespace(namespace)
        encoded = json.dumps(dict(value), ensure_ascii=False, default=str)
        if len(encoded.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise ValidationError(
                "Session document exceeds the permitted size; store the record relationally instead",
                namespace=namespace,
                bytes=len(encoded.encode("utf-8")),
                limit=MAX_DOCUMENT_BYTES,
            )
        # TTL is never optional here: an expiry set at write time is what makes
        # the retention claim in the deployment document true.
        effective_ttl = ttl_seconds or default_ttl
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=effective_ttl)).isoformat()
        self._write(namespace, key, encoded, expires, now.isoformat())

    def get(self, namespace: str, key: str) -> dict[str, Any] | None:
        self._require_namespace(namespace)
        rows = self._read(namespace, key)
        if not rows:
            return None
        return json.loads(rows[0]["value_json"])

    def delete(self, namespace: str, key: str) -> None:
        self._require_namespace(namespace)
        self._store.execute(
            f"DELETE FROM {self._table} WHERE namespace = :ns AND kv_key = :k",
            {"ns": namespace, "k": key},
        )

    def scan(self, namespace: str, key_prefix: str = "", limit: int = 500) -> list[dict[str, Any]]:
        self._require_namespace(namespace)
        now = datetime.now(timezone.utc).isoformat()
        rows = self._store.query(
            f"SELECT kv_key, value_json FROM {self._table} WHERE namespace = :ns AND kv_key LIKE :prefix"
            " AND (expires_at IS NULL OR expires_at > :now) ORDER BY kv_key LIMIT :limit",
            {"ns": namespace, "prefix": f"{key_prefix}%", "now": now, "limit": limit},
        )
        return [{"key": r["kv_key"], **json.loads(r["value_json"])} for r in rows]

    def purge_expired(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        removed = self._store.execute(
            f"DELETE FROM {self._table} WHERE expires_at IS NOT NULL AND expires_at <= :now",
            {"now": now},
        )
        LOGGER.info("kv_purge_completed", extra={"removed": removed})
        return removed

    # -------------------------------------------------------------- helpers
    @staticmethod
    def qualify(user_id: str, session_id: str) -> str:
        """Build a key that cannot collide across users.

        §6.2: a session identifier is not guaranteed globally unique, so one
        user's pins must never be reachable under another user's session.
        """
        return f"{user_id}:{session_id}"

    @staticmethod
    def _require_namespace(namespace: str) -> int:
        try:
            return NAMESPACE_TTL_SECONDS[namespace]
        except KeyError:
            raise ValidationError(
                "Unknown key/value namespace",
                namespace=namespace,
                permitted=sorted(NAMESPACE_TTL_SECONDS),
            ) from None

    # Transport seam — the only two methods a native NoSQL binding replaces.
    def _read(self, namespace: str, key: str) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        return self._store.query(
            f"SELECT value_json FROM {self._table} WHERE namespace = :ns AND kv_key = :k"
            " AND (expires_at IS NULL OR expires_at > :now)",
            {"ns": namespace, "k": key, "now": now},
        )

    def _write(self, namespace: str, key: str, encoded: str, expires: str, updated: str) -> None:
        self._store.execute(
            f"""
            INSERT INTO {self._table} (namespace, kv_key, value_json, expires_at, updated_at)
            VALUES (:ns, :k, :v, :e, :u)
            ON CONFLICT (namespace, kv_key) DO UPDATE SET
                value_json = excluded.value_json,
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            {"ns": namespace, "k": key, "v": encoded, "e": expires, "u": updated},
        )
