"""Schema-reflection parity: the static parser vs. a live SQLite database.

Phase 2 (P2-02) replaced the loader's raw ``PRAGMA table_info(...)`` call with
a portable ``DataStore.table_columns()`` capability, backed on SQLite by the
real PRAGMA and on Catalyst by parsing ``schema.sql`` + ``migrations.py``
(``infrastructure.db.schema_reflection``). This test is the parity check that
found the gap during development: parsing only ``schema.sql`` missed a
migration-added column and an entire migration-only table.
"""

from __future__ import annotations

import sqlite3

import pytest

from ksp_cip.infrastructure.db.schema_reflection import schema_columns

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def live_columns(container):
    conn = sqlite3.connect(str(container.settings.sqlite_path))
    try:
        def read(table: str) -> list[str]:
            rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            return [row[1] for row in rows]
        yield read
    finally:
        conn.close()


def test_every_table_matches_pragma_table_info(live_columns):
    parsed = schema_columns()
    checked = 0
    for table, columns in parsed.items():
        live = live_columns(table)
        if not live:
            continue  # table not created in this particular seeded DB
        assert live == columns, f"{table}: parsed {columns} != live {live}"
        checked += 1
    assert checked > 40, "the parity check did not actually examine the curated schema"


def test_a_migration_added_column_is_included(live_columns):
    """Regression guard: cip_user_account.external_subject (migration 2) must
    appear even though it is not declared in the base schema.sql."""
    assert "external_subject" in schema_columns()["cip_user_account"]
    assert "external_subject" in live_columns("cip_user_account")


def test_a_migration_only_table_is_included(live_columns):
    """Regression guard: cip_event_calendar (migration 3) is a whole new
    table, not a column, and must still be reachable."""
    assert "event_id" in schema_columns()["cip_event_calendar"]
    assert "event_id" in live_columns("cip_event_calendar")


def test_the_loader_uses_the_port_not_a_raw_pragma(container):
    """The defect this replaced: a repository constructing dialect-specific
    introspection SQL directly, which the Catalyst adapter cannot run."""
    from ksp_cip.application.pipeline.loader import BatchWriter, Loader

    writer = BatchWriter(container.filestore, container.control)
    loader = Loader(container.store, container.control, writer)
    columns = loader._table_columns("curated_CaseMaster")  # noqa: SLF001 - testing the seam directly
    assert "CaseMasterID" in columns
    assert "CrimeNo" in columns
