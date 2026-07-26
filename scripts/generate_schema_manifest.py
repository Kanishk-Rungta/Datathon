#!/usr/bin/env python3
"""Generate the Catalyst Data Store provisioning manifest (Phase 2, P2-01).

Reads ``backend/ksp_cip/infrastructure/db/schema.sql`` plus every entry in
``migrations.py`` and emits a JSON manifest — table, column, type,
nullable/required, primary key, foreign keys, and index requirements — for a
human to provision Catalyst Data Store tables from. This is a one-time
provisioning-support tool, not runtime code: the application itself only ever
needs column *names* (see ``infrastructure.db.schema_reflection``), which is a
narrower, separately-tested concern.

This script does not touch a live Catalyst project. It has no credentials and
makes no network call. Its output is a document a person reads while creating
tables through the Catalyst console/CLI/IaC workflow, per P2-01.

Usage:
    python scripts/generate_schema_manifest.py
    python scripts/generate_schema_manifest.py --output docs/deployment/catalyst-schema-manifest.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from ksp_cip.infrastructure.db.schema_reflection import (  # noqa: E402
    SCHEMA_PATH,
    _split_top_level,
    _strip_sql_comments,
)

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\((.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_CREATE_INDEX_RE = re.compile(
    r"CREATE\s+(UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)\s+ON\s+(\w+)\s*\(([^)]+)\)",
    re.IGNORECASE,
)
_ALTER_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)\s+([\w()]+)",
    re.IGNORECASE,
)
_TABLE_LEVEL_KEYWORDS = {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"}


def parse_column(fragment: str) -> dict[str, object] | None:
    tokens = fragment.strip().split()
    if not tokens:
        return None
    name = tokens[0].strip('"`[]')
    if name.upper() in _TABLE_LEVEL_KEYWORDS:
        return None
    type_token = tokens[1] if len(tokens) > 1 else "TEXT"
    upper = fragment.upper()
    references_match = re.search(r"REFERENCES\s+(\w+)\s*\(([^)]+)\)", fragment, re.IGNORECASE)
    return {
        "name": name,
        "type": type_token,
        "primary_key": "PRIMARY KEY" in upper,
        "not_null": "NOT NULL" in upper,
        "references": (
            {"table": references_match.group(1), "column": references_match.group(2).strip()}
            if references_match else None
        ),
    }


def parse_table(name: str, body: str) -> dict[str, object]:
    columns: list[dict[str, object]] = []
    table_primary_key: list[str] = []
    for fragment in _split_top_level(body):
        stripped = fragment.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper.startswith("PRIMARY KEY"):
            inside = re.search(r"\(([^)]+)\)", stripped)
            if inside:
                table_primary_key.extend(part.strip().strip('"`[]') for part in inside.group(1).split(","))
            continue
        if upper.split(None, 1)[0] in _TABLE_LEVEL_KEYWORDS:
            continue
        column = parse_column(stripped)
        if column:
            columns.append(column)

    for column in columns:
        if column["name"] in table_primary_key:
            column["primary_key"] = True

    return {"table": name, "columns": columns}


def build_manifest() -> dict[str, object]:
    from ksp_cip.infrastructure.db.migrations import MIGRATIONS

    base_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    migration_sql = [sql for _v, _d, sql in MIGRATIONS]
    full_sql = _strip_sql_comments(base_sql + "\n" + "\n".join(migration_sql))

    tables: dict[str, dict[str, object]] = {}
    for match in _CREATE_TABLE_RE.finditer(full_sql):
        parsed = parse_table(match.group(1), match.group(2))
        tables[parsed["table"]] = parsed

    for match in _ALTER_ADD_COLUMN_RE.finditer(full_sql):
        table, column_name, column_type = match.group(1), match.group(2), match.group(3)
        if table in tables:
            tables[table]["columns"].append({
                "name": column_name, "type": column_type,
                "primary_key": False, "not_null": False, "references": None,
                "added_by": "migration",
            })

    indexes: list[dict[str, object]] = []
    for match in _CREATE_INDEX_RE.finditer(full_sql):
        is_unique, index_name, table_name, index_columns = match.groups()
        indexes.append({
            "name": index_name,
            "table": table_name,
            "unique": bool(is_unique),
            "columns": [c.strip().strip('"`[]') for c in index_columns.split(",")],
        })

    return {
        "generated_from": ["backend/ksp_cip/infrastructure/db/schema.sql", "migrations.py"],
        "table_count": len(tables),
        "tables": [tables[name] for name in sorted(tables)],
        "indexes": indexes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path,
                        default=REPO_ROOT / "docs" / "deployment" / "catalyst-schema-manifest.json")
    args = parser.parse_args()

    manifest = build_manifest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=False), encoding="utf-8")
    print(f"Wrote {manifest['table_count']} tables and {len(manifest['indexes'])} indexes to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
