"""Schema bootstrap and forward-only migrations.

The base schema lives in ``schema.sql``. Incremental migrations are appended
to ``MIGRATIONS`` as ``(version, description, sql)`` and applied in order.
Applied versions are recorded in ``ctl_schema_version`` so re-running is a
no-op (architecture §14.2: versioned, forward-only).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ...domain.ports import DataStore

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

MIGRATIONS: list[tuple[int, str, str]] = [
    # (version, description, sql) — base schema is version 1 and is applied
    # from schema.sql; further changes are appended here.
    (
        2,
        "external identity subject on cip_user_account (Catalyst Authentication mapping)",
        """
        ALTER TABLE cip_user_account ADD COLUMN external_subject TEXT;
        CREATE INDEX IF NOT EXISTS ix_user_external_subject
            ON cip_user_account (external_subject);
        """,
    ),
    (
        3,
        "event calendar reference table for event-window comparison",
        """
        CREATE TABLE IF NOT EXISTS cip_event_calendar (
            event_id        TEXT PRIMARY KEY,
            event_name      TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            date_from       TEXT NOT NULL,
            date_to         TEXT NOT NULL,
            district_id     INTEGER,
            unit_id         INTEGER,
            source          TEXT NOT NULL,
            data_quality    TEXT NOT NULL DEFAULT 'unverified',
            approval_status TEXT NOT NULL DEFAULT 'pending',
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_event_window
            ON cip_event_calendar (date_from, date_to);
        CREATE INDEX IF NOT EXISTS ix_event_approval
            ON cip_event_calendar (approval_status);
        """,
    ),
]


def _ensure_version_table(store: DataStore) -> None:
    store.executescript(
        """
        CREATE TABLE IF NOT EXISTS ctl_schema_version (
            version     INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at  TEXT NOT NULL
        );
        """
    )


def current_version(store: DataStore) -> int:
    _ensure_version_table(store)
    rows = store.query("SELECT COALESCE(MAX(version), 0) AS v FROM ctl_schema_version")
    return int(rows[0]["v"]) if rows else 0


def apply_migrations(store: DataStore) -> list[int]:
    """Apply the base schema and every pending migration. Idempotent."""
    _ensure_version_table(store)
    applied: list[int] = []
    version = current_version(store)
    now = datetime.now(timezone.utc).isoformat()

    if version < 1:
        store.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        store.execute(
            "INSERT INTO ctl_schema_version (version, description, applied_at)"
            " VALUES (1, :d, :t)",
            {"d": "base schema (curated mirror + cip + ext + audit)", "t": now},
        )
        applied.append(1)
        version = 1

    for target, description, sql in MIGRATIONS:
        if target <= version:
            continue
        store.executescript(sql)
        store.execute(
            "INSERT INTO ctl_schema_version (version, description, applied_at)"
            " VALUES (:v, :d, :t)",
            {"v": target, "d": description, "t": now},
        )
        applied.append(target)
    return applied
