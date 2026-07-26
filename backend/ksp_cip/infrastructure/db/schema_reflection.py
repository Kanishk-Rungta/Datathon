"""Static schema reflection over ``schema.sql``.

Phase 2 (P2-02) of ``implementationv2-phases-0-2.md`` asks for a portable
``DataStore.table_columns()`` capability so the loader stops constructing
``PRAGMA table_info(...)`` directly — a statement the Catalyst adapter cannot
run at all (``CatalystDataStore.query`` refuses any ``PRAGMA``).

SQLite has a live, authoritative catalog (``PRAGMA table_info``), so
``SQLiteDataStore.table_columns`` keeps using it directly — it is cheap and
reflects the actual runtime schema, including anything a migration changed.

Catalyst has no equivalent live introspection endpoint documented, so
``CatalystDataStore.table_columns`` parses ``schema.sql`` plus every
``MIGRATIONS`` entry in ``migrations.py`` — the "schema manifest" the plan
refers to. This is only as accurate as the last provisioning pass kept
Catalyst in sync with those files; that limitation is explicit, not hidden —
see ``docs/deployment/catalyst-schema.md``.

A cross-check against a live SQLite database's own ``PRAGMA table_info``
during development caught exactly the failure mode this docstring warns
about: parsing only ``schema.sql`` missed ``cip_user_account.external_subject``,
added by migration 2's ``ALTER TABLE ... ADD COLUMN``. Both migration
mechanisms (new ``CREATE TABLE`` and ``ALTER TABLE ... ADD COLUMN``) are
therefore folded in below, not just the base file.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\((.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_ALTER_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)",
    re.IGNORECASE,
)
_TABLE_LEVEL_KEYWORDS = {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"}


def _split_top_level(body: str) -> list[str]:
    """Split a ``CREATE TABLE`` body on commas outside nested parentheses."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def _strip_sql_comments(text: str) -> str:
    return re.sub(r"--[^\n]*", "", text)


def parse_schema_columns(schema_sql: str) -> dict[str, list[str]]:
    """Return ``{table_name: [column_name, ...]}`` from a ``schema.sql`` body."""
    cleaned = _strip_sql_comments(schema_sql)
    tables: dict[str, list[str]] = {}
    for match in _CREATE_TABLE_RE.finditer(cleaned):
        table_name = match.group(1)
        columns: list[str] = []
        for fragment in _split_top_level(match.group(2)):
            token = fragment.strip()
            if not token:
                continue
            first_word = token.split(None, 1)[0].strip('"`[]').upper()
            if first_word in _TABLE_LEVEL_KEYWORDS:
                continue
            columns.append(token.split(None, 1)[0].strip('"`[]'))
        tables[table_name] = columns
    return tables


def _apply_alter_add_column(tables: dict[str, list[str]], sql: str) -> None:
    for match in _ALTER_ADD_COLUMN_RE.finditer(_strip_sql_comments(sql)):
        table, column = match.group(1), match.group(2)
        columns = tables.setdefault(table, [])
        if column not in columns:
            columns.append(column)


@lru_cache(maxsize=1)
def schema_columns() -> dict[str, list[str]]:
    """Parse of the base schema plus every migration, cached for the process
    lifetime (this file only changes with a code deployment, not at runtime).

    Folds in both migration mechanisms this codebase uses: a new
    ``CREATE TABLE IF NOT EXISTS`` (picked up because migration SQL is parsed
    with the same table-extraction pass as the base schema) and
    ``ALTER TABLE ... ADD COLUMN`` on an existing table (applied as a patch
    afterward, since the base regex only matches whole ``CREATE TABLE``
    statements).
    """
    from .migrations import MIGRATIONS

    base_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    migration_sql = [sql for _version, _description, sql in MIGRATIONS]

    tables = parse_schema_columns(base_sql + "\n" + "\n".join(migration_sql))
    for sql in migration_sql:
        _apply_alter_add_column(tables, sql)
    return tables
