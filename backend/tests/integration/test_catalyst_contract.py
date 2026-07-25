"""Contract tests for the Catalyst adapter.

No network is used. These assert the translation layer behaves — which is
where the risk actually lives, since ZCQL is not SQL and a silent
approximation would corrupt data rather than fail loudly.
"""

import pytest

from ksp_cip.domain.errors import CIPError
from ksp_cip.infrastructure.catalyst import bind_named, quote_literal
from ksp_cip.infrastructure.catalyst.datastore import (
    _parse_insert,
    _parse_insert_columns,
    _split_top_level,
)


class TestLiteralQuoting:
    def test_strings_are_escaped(self):
        assert quote_literal("O'Brien") == "'O\\'Brien'"

    def test_backslashes_are_escaped_first(self):
        assert quote_literal("a\\b") == "'a\\\\b'"

    def test_none_becomes_null(self):
        assert quote_literal(None) == "NULL"

    def test_booleans_are_lowercase_keywords(self):
        assert quote_literal(True) == "true"
        assert quote_literal(False) == "false"

    def test_numbers_pass_through_unquoted(self):
        assert quote_literal(42) == "42"

    @pytest.mark.parametrize("value", [{"a": 1}, [1, 2], b"bytes", object()])
    def test_non_scalars_are_refused(self, value):
        """Silently encoding a structure here is how corrupt rows get written."""
        with pytest.raises(CIPError):
            quote_literal(value)


class TestParameterBinding:
    def test_named_parameters_are_replaced(self):
        assert bind_named("SELECT * FROM t WHERE a = :a", {"a": 1}) == "SELECT * FROM t WHERE a = 1"

    def test_longer_names_bind_before_their_prefixes(self):
        """`:id` must not eat the front of `:id_list`."""
        rendered = bind_named("SELECT :id, :id_list", {"id": 1, "id_list": 2})
        assert rendered == "SELECT 1, 2"

    def test_an_unbound_parameter_is_an_error(self):
        with pytest.raises(CIPError):
            bind_named("SELECT * FROM t WHERE a = :a", {})

    def test_a_colon_inside_a_literal_is_not_a_parameter(self):
        rendered = bind_named("SELECT * FROM t WHERE a = :a", {"a": "12:30"})
        assert rendered.endswith("'12:30'")

    def test_injection_through_a_parameter_is_neutralised(self):
        rendered = bind_named("SELECT * FROM t WHERE a = :a", {"a": "x' OR '1'='1"})
        assert "OR '1'='1" not in rendered.replace("\\'", "")


class TestInsertParsing:
    def test_columns_and_table_are_extracted(self):
        table, columns = _parse_insert_columns('INSERT INTO "t" (a, b) VALUES (1, 2)')
        assert table == "t"
        assert columns == ["a", "b"]

    def test_values_are_mapped_back_to_python(self):
        table, values = _parse_insert(
            "INSERT INTO t (a, b, c, d) VALUES (1, 'text', NULL, 2.5)"
        )
        assert table == "t"
        assert values == {"a": 1, "b": "text", "c": None, "d": 2.5}

    def test_commas_inside_literals_do_not_split_values(self):
        parts = _split_top_level("1, 'a, b', 3")
        assert parts == ["1", "'a, b'", "3"]

    def test_escaped_quotes_inside_literals_are_preserved(self):
        parts = _split_top_level("'O\\'Brien', 2")
        assert len(parts) == 2


class TestUnsupportedOperations:
    def test_pragma_is_refused_rather_than_approximated(self, monkeypatch):
        from ksp_cip.config import Settings
        from ksp_cip.infrastructure.catalyst.datastore import CatalystDataStore

        settings = Settings(catalyst_project_id="p")
        store = CatalystDataStore(settings, auth=object())
        with pytest.raises(CIPError):
            store.query("PRAGMA table_info(curated_CaseMaster)")

    def test_transaction_is_documented_as_a_no_op(self):
        from ksp_cip.config import Settings
        from ksp_cip.infrastructure.catalyst.datastore import CatalystDataStore

        store = CatalystDataStore(Settings(catalyst_project_id="p"), auth=object())
        assert "no multi-row transaction" in (store.transaction.__doc__ or "").lower() or \
               "no multi-row transaction primitive" in (store.transaction.__doc__ or "")
        with store.transaction():
            pass
