"""Static schema reflection parser (Phase 2, P2-02) — synthetic, fast tests.

The live-database parity check (this parser vs. a real SQLite ``PRAGMA
table_info``, across every table including migration-added ones) lives in
``tests/integration/test_schema_reflection_parity.py`` because it needs the
seeded container fixture. This file only exercises the regex/parsing logic
against small synthetic SQL strings.
"""

from __future__ import annotations

from ksp_cip.infrastructure.db.schema_reflection import parse_schema_columns


class TestParserOnASyntheticSchema:
    def test_a_simple_table_is_parsed(self):
        tables = parse_schema_columns("CREATE TABLE IF NOT EXISTS t (a INTEGER, b TEXT);")
        assert tables["t"] == ["a", "b"]

    def test_table_level_constraints_are_not_columns(self):
        sql = (
            "CREATE TABLE IF NOT EXISTS t (\n"
            "  a INTEGER,\n"
            "  b TEXT,\n"
            "  PRIMARY KEY (a),\n"
            "  FOREIGN KEY (b) REFERENCES other (id),\n"
            "  CHECK (a > 0)\n"
            ");"
        )
        assert parse_schema_columns(sql)["t"] == ["a", "b"]

    def test_inline_references_do_not_add_a_phantom_column(self):
        sql = "CREATE TABLE IF NOT EXISTS t (a INTEGER, b INTEGER REFERENCES other (id));"
        assert parse_schema_columns(sql)["t"] == ["a", "b"]

    def test_a_comment_line_is_ignored(self):
        sql = (
            "CREATE TABLE IF NOT EXISTS t (\n"
            "  a INTEGER, -- a comment mentioning FOREIGN KEY and CHECK\n"
            "  b TEXT\n"
            ");"
        )
        assert parse_schema_columns(sql)["t"] == ["a", "b"]

    def test_a_comma_inside_a_check_expression_does_not_split_it(self):
        sql = (
            "CREATE TABLE IF NOT EXISTS t (\n"
            "  a INTEGER,\n"
            "  CHECK (a IN (1, 2, 3))\n"
            ");"
        )
        assert parse_schema_columns(sql)["t"] == ["a"]

    def test_multiple_tables_are_all_parsed(self):
        sql = (
            "CREATE TABLE IF NOT EXISTS one (a INTEGER);\n"
            "CREATE TABLE IF NOT EXISTS two (b TEXT, c TEXT);\n"
        )
        tables = parse_schema_columns(sql)
        assert tables["one"] == ["a"]
        assert tables["two"] == ["b", "c"]
