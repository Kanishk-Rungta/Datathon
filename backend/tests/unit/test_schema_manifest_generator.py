"""The Catalyst provisioning-manifest generator (Phase 2, P2-01).

This is a build-time tool, not runtime code, so it lives under ``scripts/``
rather than ``ksp_cip``. It is still tested: the property that matters is that
its table/column inventory agrees with ``schema_reflection.schema_columns()``
(the runtime-facing parser) — two independent parsers of the same schema
silently drifting apart would be worse than either one being wrong alone.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate_schema_manifest.py"
MANIFEST_PATH = REPO_ROOT / "docs" / "deployment" / "catalyst-schema-manifest.json"

#: What an operator runs to make a failure in this module go away.
REGENERATE = (
    "python scripts/generate_schema_manifest.py && "
    "python scripts/generate_catalyst_console_checklist.py"
)


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


class TestTheCommittedManifestIsCurrent:
    """The manifest *file* must match the schema, not just the generator.

    Everything above compares the generator against the runtime parser, which
    is a property of two code paths and holds no matter how stale the checked-in
    JSON is. That gap is not theoretical: ``ext_socioeconomic_indicator`` was
    added by migration 4 and the committed manifest sat at 47 tables for the
    whole of the V3.1 branch, while every test here passed.

    That file is not documentation. ``scripts/provision_catalyst_datastore.js``
    and ``scripts/load_catalyst_data.js`` are both entirely manifest-driven and
    read this exact path, so a table missing from it is never created in
    Catalyst and never loaded -- and the deployment comes up healthy with the
    feature that reads it returning 500. A stale manifest is a deployment
    defect, so it fails here rather than in production.
    """

    def test_the_committed_manifest_matches_a_fresh_regeneration(self):
        generator = _load_generator()
        fresh = generator.build_manifest()
        committed = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

        fresh_tables = {t["table"] for t in fresh["tables"]}
        committed_tables = {t["table"] for t in committed["tables"]}
        assert committed_tables == fresh_tables, (
            "The committed Catalyst manifest is out of date.\n"
            f"  missing from the file: {sorted(fresh_tables - committed_tables)}\n"
            f"  no longer in the schema: {sorted(committed_tables - fresh_tables)}\n"
            f"Regenerate it: {REGENERATE}"
        )

        fresh_columns = {t["table"]: [c["name"] for c in t["columns"]] for t in fresh["tables"]}
        committed_columns = {t["table"]: [c["name"] for c in t["columns"]] for t in committed["tables"]}
        for table in sorted(fresh_tables):
            assert committed_columns[table] == fresh_columns[table], (
                f"Columns for '{table}' in the committed manifest are out of date.\n"
                f"Regenerate it: {REGENERATE}"
            )

        # Indexes last: a column diff is the more useful message when both are
        # wrong, and provisioning reads the index list too.
        assert committed["indexes"] == fresh["indexes"], (
            "Index definitions in the committed manifest are out of date.\n"
            f"Regenerate it: {REGENERATE}"
        )

    def test_the_committed_manifest_is_byte_for_byte_what_the_generator_writes(self):
        """Catches a hand-edit that happens to preserve names and ordering.

        The provisioning scripts read types, nullability, uniqueness and foreign
        keys as well as names, so name-level agreement is not enough to trust
        the file. Comparing parsed JSON rather than raw bytes keeps this
        indifferent to trailing whitespace and line endings, which differ
        between the platforms this repository is developed on.
        """
        generator = _load_generator()
        committed = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        assert committed == generator.build_manifest(), (
            "The committed Catalyst manifest differs from generator output beyond "
            f"table and column names -- a type, key or index detail has drifted.\n"
            f"Regenerate it: {REGENERATE}"
        )
