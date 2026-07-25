"""SQLite implementation of the :class:`DataStore` port.

This is the adapter that runs locally and in CI. It is deliberately written
against the same narrow interface as the Catalyst Data Store adapter so that
no application code can depend on SQLite specifics.

Concurrency model: one connection per thread (FastAPI's threadpool executes
sync route handlers on worker threads), WAL journaling, and a shared lock for
write transactions so nested writers cannot interleave.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from ...domain.errors import CIPError, ConflictError

_PARAM_RE = re.compile(r":(\w+)")


class SQLiteDataStore:
    """Thread-safe SQLite adapter."""

    def __init__(self, path: Path, *, timeout: float = 30.0) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._timeout = timeout
        self._local = threading.local()
        self._write_lock = threading.RLock()

    # ------------------------------------------------------------ plumbing
    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._path),
            timeout=self._timeout,
            check_same_thread=False,
            isolation_level=None,  # explicit transaction control
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = %d" % int(self._timeout * 1000))
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.create_function("json_len", 1, _json_len, deterministic=True)
        return conn

    @property
    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -------------------------------------------------------------- public
    def query(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        cursor = self._execute(sql, params)
        try:
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def query_one(self, sql: str, params: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> int:
        with self._write_lock:
            cursor = self._execute(sql, params)
            try:
                return cursor.rowcount
            finally:
                cursor.close()

    def execute_many(self, sql: str, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        with self._write_lock:
            conn = self.connection
            try:
                cursor = conn.executemany(sql, [dict(row) for row in rows])
            except sqlite3.IntegrityError as exc:  # pragma: no cover - defensive
                raise ConflictError(str(exc), sql=_redact(sql)) from exc
            except sqlite3.Error as exc:  # pragma: no cover - defensive
                raise CIPError(f"SQLite error: {exc}", sql=_redact(sql)) from exc
            count = cursor.rowcount
            cursor.close()
            return count

    def executescript(self, script: str) -> None:
        with self._write_lock:
            self.connection.executescript(script)

    @contextmanager
    def transaction(self) -> Iterator["SQLiteDataStore"]:
        with self._write_lock:
            conn = self.connection
            depth = getattr(self._local, "depth", 0)
            if depth == 0:
                conn.execute("BEGIN IMMEDIATE")
            self._local.depth = depth + 1
            try:
                yield self
            except BaseException:
                self._local.depth = depth
                if depth == 0:
                    conn.execute("ROLLBACK")
                raise
            else:
                self._local.depth = depth
                if depth == 0:
                    conn.execute("COMMIT")

    # ------------------------------------------------------------ internals
    def _execute(self, sql: str, params: Mapping[str, Any] | None) -> sqlite3.Cursor:
        prepared = _prepare_params(sql, params or {})
        try:
            return self.connection.execute(sql, prepared)
        except sqlite3.IntegrityError as exc:
            raise ConflictError(str(exc), sql=_redact(sql)) from exc
        except sqlite3.Error as exc:
            raise CIPError(f"SQLite error: {exc}", sql=_redact(sql)) from exc


def _prepare_params(sql: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Coerce Python values into SQLite-native types.

    Also drops keys that do not appear in the statement, which lets callers
    pass a superset dict (e.g. a full filter object) safely.
    """
    used = set(_PARAM_RE.findall(sql))
    prepared: dict[str, Any] = {}
    for key, value in params.items():
        if used and key not in used:
            continue
        prepared[key] = _coerce(value)
    missing = used - set(prepared)
    if missing:
        raise CIPError(f"Missing SQL parameters: {sorted(missing)}", sql=_redact(sql))
    return prepared


def _coerce(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, str, bytes)):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (list, tuple, dict, set)):
        return json.dumps(sorted(value) if isinstance(value, set) else value, ensure_ascii=False, default=str)
    return str(value)


def _json_len(payload: Any) -> int:
    try:
        return len(json.loads(payload or "[]"))
    except (TypeError, ValueError):
        return 0


def _redact(sql: str) -> str:
    return " ".join(sql.split())[:200]
