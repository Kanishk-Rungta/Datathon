"""The Catalyst provisioning-manifest generator (Phase 2, P2-01).

This is a build-time tool, not runtime code, so it lives under ``scripts/``
rather than ``ksp_cip``. It is still tested: the property that matters is that
its table/column inventory agrees with ``schema_reflection.schema_columns()``
(the runtime-facing parser) — two independent parsers of the same schema
silently drifting apart would be worse than either one being wrong alone.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate_schema_manifest.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_schema_manifest", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_manifest_table_names_match_the_runtime_parser():
    from ksp_cip.infrastructure.db.schema_reflection import schema_columns

    generator = _load_generator()
    manifest = generator.build_manifest()
    manifest_tables = {t["table"] for t in manifest["tables"]}
    runtime_tables = set(schema_columns())
    assert manifest_tables == runtime_tables


def test_manifest_column_names_match_the_runtime_parser_per_table():
    from ksp_cip.infrastructure.db.schema_reflection import schema_columns

    generator = _load_generator()
    manifest = generator.build_manifest()
    runtime = schema_columns()
    for entry in manifest["tables"]:
        manifest_columns = [c["name"] for c in entry["columns"]]
        assert manifest_columns == runtime[entry["table"]], entry["table"]


def test_a_known_primary_key_is_flagged():
    generator = _load_generator()
    manifest = generator.build_manifest()
    case_master = next(t for t in manifest["tables"] if t["table"] == "curated_CaseMaster")
    pk_columns = [c["name"] for c in case_master["columns"] if c["primary_key"]]
    assert pk_columns == ["CaseMasterID"]


def test_a_known_foreign_key_is_captured():
    generator = _load_generator()
    manifest = generator.build_manifest()
    case_master = next(t for t in manifest["tables"] if t["table"] == "curated_CaseMaster")
    station = next(c for c in case_master["columns"] if c["name"] == "PoliceStationID")
    assert station["references"] == {"table": "curated_Unit", "column": "UnitID"}


def test_at_least_one_index_is_captured():
    generator = _load_generator()
    manifest = generator.build_manifest()
    assert manifest["indexes"]
    assert all("table" in entry and "columns" in entry for entry in manifest["indexes"])
