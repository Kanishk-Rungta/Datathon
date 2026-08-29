"""Zoho Catalyst Data Store adapter.

Implements the same :class:`DataStore` protocol as the SQLite adapter, so the
application layer is unchanged between local development and a Catalyst
deployment. Switching is one environment variable: ``KSPCIP_DATASTORE_BACKEND=catalyst``.

Three real differences from SQLite are handled here rather than leaking upward:

* **ZCQL is not SQL.** It has no ``PRAGMA``, no ``ON CONFLICT``, and a limited
  expression grammar. Upserts are therefore read-then-write, and the adapter
  refuses statements it cannot faithfully translate instead of silently
  approximating them.
* **Named parameters must be inlined.** ZCQL takes a query string, so binding
  is done here with strict escaping — the same discipline the SQLite adapter
  gets from the driver. Only scalars are accepted.
* **Row limits.** ZCQL caps rows per response, so ``query`` paginates.

This adapter is written against the documented REST API and is exercised by
contract tests that assert translation behaviour without a network. It has not
been run against a live Catalyst project in this build; that is stated plainly
rather than implied otherwise.
"""

from __future__ import annotations

import json
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Sequence
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from ...config import Settings
from ...domain.errors import CIPError, ProviderError
from ..observability import get_logger

LOGGER = get_logger(__name__)

ZCQL_PAGE_SIZE = 300
TOKEN_SAFETY_WINDOW_SECONDS = 120


class CatalystAuth:
    """OAuth refresh-token flow with in-process caching."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at: float = 0.0

    def token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires_at - TOKEN_SAFETY_WINDOW_SECONDS:
                return self._token
            self._token = self._refresh()
            return self._token

    def _refresh(self) -> str:
        settings = self._settings
        missing = [
            name for name, value in (
                ("catalyst_oauth_client_id", settings.catalyst_oauth_client_id),
                ("catalyst_oauth_client_secret", settings.catalyst_oauth_client_secret),
                ("catalyst_oauth_refresh_token", settings.catalyst_oauth_refresh_token),
                ("catalyst_project_id", settings.catalyst_project_id),
            ) if not value
        ]
        if missing:
            raise ProviderError(
                "Catalyst credentials are not configured",
                provider="catalyst",
                missing=missing,
            )
        payload = urllib_parse.urlencode({
            "refresh_token": settings.catalyst_oauth_refresh_token,
            "client_id": settings.catalyst_oauth_client_id,
            "client_secret": settings.catalyst_oauth_client_secret,
            "grant_type": "refresh_token",
        }).encode("utf-8")
        url = f"{settings.catalyst_accounts_url.rstrip('/')}/oauth/v2/token"
        request = urllib_request.Request(url, data=payload, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib_request.urlopen(request, timeout=30) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib_error.URLError as exc:  # pragma: no cover - network path
            raise ProviderError("Catalyst token refresh failed", provider="catalyst") from exc
        if "access_token" not in body:
            raise ProviderError("Catalyst token refresh returned no access token", provider="catalyst",
                                detail=body.get("error"))
        self._expires_at = time.time() + int(body.get("expires_in", 3600))
        return str(body["access_token"])


def quote_literal(value: Any) -> str:
    """Inline a scalar into ZCQL with strict escaping.

    Refusing non-scalars is deliberate: silently JSON-encoding a dict here is
    how injection bugs and corrupt rows get introduced.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (bytes, bytearray)):
        raise CIPError("Binary values must be base64-encoded before reaching ZCQL")
    if not isinstance(value, str):
        raise CIPError(f"ZCQL cannot bind a value of type {type(value).__name__}")
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def bind_named(sql: str, params: Mapping[str, Any]) -> str:
    """Replace ``:name`` placeholders with escaped literals, longest name first."""
    rendered = sql
    for name in sorted(params, key=len, reverse=True):
        rendered = rendered.replace(f":{name}", quote_literal(params[name]))
    if ":" in _strip_literals(rendered):
        raise CIPError("Unbound parameter remains after ZCQL binding", sql=sql[:200])
    return rendered


def _strip_literals(sql: str) -> str:
    out: list[str] = []
    inside = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if char == "\\" and inside:
            index += 2
            continue
        if char == "'":
            inside = not inside
        elif not inside:
            out.append(char)
        index += 1
    return "".join(out)


_SELECT_LIST_RE = re.compile(r"^\s*SELECT\s+(?P<cols>.*?)\s+FROM\s", re.IGNORECASE | re.DOTALL)
_ALIAS_RE = re.compile(r"^(?P<expr>.*?)\s+AS\s+(?P<alias>\w+)\s*$", re.IGNORECASE | re.DOTALL)


def _split_select_items(columns: str) -> list[str]:
    """Split a SELECT list on commas that are not inside parentheses."""
    items, depth, current = [], 0, []
    for char in columns:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            items.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        items.append("".join(current))
    return items


def _normalise_key(expr: str) -> str:
    """The key ZCQL will actually return for a selected expression."""
    expr = expr.strip()
    if re.fullmatch(r"[\w.]+", expr):
        return expr.rsplit(".", 1)[-1]        # `c.CaseMasterID` -> `CaseMasterID`
    return re.sub(r"\s+", "", expr)            # `COUNT( ROWID )` -> `COUNT(ROWID)`


def _translate_aggregates(statement: str) -> tuple[str, dict[str, str]]:
    """Bring a SELECT into the dialect ZCQL actually accepts.

    Two differences, both invisible until a query runs against a live project:

    * ``COUNT(*)`` is rejected outright — *"\\* is not supported in Functions.
      Please give a valid column name"*. Every table has ``ROWID``, so
      ``COUNT(ROWID)`` is the portable equivalent.
    * **``AS alias`` is ignored entirely.** Rows come back keyed by the
      underlying column or expression, so ``SELECT c.CaseMasterID AS
      case_master_id`` yields ``CaseMasterID`` and a caller reading
      ``row["case_master_id"]`` raises ``KeyError``. This is not limited to
      aggregates; it applies to every aliased column.

    Handled here rather than in the call sites so repositories stay written in
    one dialect, the same reasoning as the upsert emulation below.
    """
    translated = re.sub(r"COUNT\s*\(\s*\*\s*\)", "COUNT(ROWID)", statement, flags=re.IGNORECASE)

    match = _SELECT_LIST_RE.search(translated)
    if not match:
        return translated, {}

    aliases: dict[str, str] = {}
    expressions: dict[str, str] = {}
    for item in _split_select_items(match.group("cols")):
        aliased = _ALIAS_RE.match(item.strip())
        if aliased:
            expr, alias = aliased.group("expr").strip(), aliased.group("alias")
            aliases[_normalise_key(expr)] = alias
            expressions[alias] = expr
    return _expand_alias_references(translated, expressions), aliases


def _expand_alias_references(statement: str, expressions: dict[str, str]) -> str:
    """Replace alias references in GROUP BY / ORDER BY with their expressions.

    SQLite lets a GROUP BY or ORDER BY name a SELECT alias; ZCQL does not, and
    reports it as a missing column — *"Unkown Table c or Unkown Column
    sub_head_id in GROUP BY"*. Roughly ten repository queries are written that
    way, so the substitution happens here rather than in each of them: the SQL
    stays readable, and there is one dialect rule in one place.
    """
    if not expressions:
        return statement

    def rewrite(match: re.Match[str]) -> str:
        clause = match.group(0)
        for alias, expr in expressions.items():
            if alias != expr:
                clause = re.sub(rf"\b{re.escape(alias)}\b", expr, clause)
        return clause

    return re.sub(r"(?:GROUP|ORDER)\s+BY\s+[^)]*?(?=\s+(?:GROUP|ORDER|LIMIT)\s|\s*$)",
                  rewrite, statement, flags=re.IGNORECASE)


#: A caller's own trailing `LIMIT n [OFFSET m]`, in SQLite's spelling.
_TRAILING_LIMIT_RE = re.compile(
    r"\s+LIMIT\s+(?P<limit>\d+)(?:\s+OFFSET\s+(?P<offset>\d+))?\s*$", re.IGNORECASE)


def _split_limit(statement: str) -> tuple[str, int | None, int]:
    """Separate a caller's own LIMIT from the statement, for re-application.

    17 repository queries end in ``LIMIT :limit`` (some with ``OFFSET``). The
    pager used to append its own clause regardless, producing
    ``... LIMIT 50 LIMIT 0, 300`` — which ZCQL rejects with *"ZCQL CANNOT HAVE
    MORE THAN 300 ROWS in LIMIT"*, a message that points at the page size
    rather than the duplicated clause.

    Returning the caller's bound and offset lets the pager honour both: it
    stops once that many rows are collected, and still pages underneath when
    the requested bound exceeds what ZCQL will return in one call.
    """
    match = _TRAILING_LIMIT_RE.search(statement)
    if not match:
        return statement, None, 0
    limit = int(match.group("limit"))
    offset = int(match.group("offset") or 0)
    return statement[: match.start()], limit, offset


def _restore_aliases(rows: list[dict[str, Any]], aliases: dict[str, str]) -> list[dict[str, Any]]:
    """Re-key aggregate columns to the alias the caller asked for."""
    if not aliases:
        return rows
    restored: list[dict[str, Any]] = []
    for row in rows:
        out = dict(row)
        for key, value in row.items():
            alias = aliases.get(_normalise_key(key))
            if alias is not None and alias not in out:
                out[alias] = value
        restored.append(out)
    return restored


class CatalystDataStore:
    """DataStore backed by Catalyst Data Store tables via ZCQL."""

    backend = "catalyst"

    def __init__(self, settings: Settings, auth: CatalystAuth | None = None) -> None:
        self._settings = settings
        self._auth = auth or CatalystAuth(settings)
        self._base = (
            f"{settings.catalyst_base_url.rstrip('/')}"
            f"/baas/v1/project/{settings.catalyst_project_id}"
        )

    # ------------------------------------------------------------- protocol
    def query(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        statement = bind_named(sql, params or {})
        if statement.lstrip().upper().startswith("PRAGMA"):
            raise CIPError("PRAGMA is not available on the Catalyst Data Store", sql=statement[:120])
        statement, aliases = _translate_aggregates(statement)
        statement, want, offset = _split_limit(statement)

        rows: list[dict[str, Any]] = []
        while True:
            remaining = ZCQL_PAGE_SIZE if want is None else min(ZCQL_PAGE_SIZE, want - len(rows))
            if remaining <= 0:
                break
            page = self._zcql(f"{statement} LIMIT {offset}, {remaining}")
            rows.extend(_restore_aliases(page, aliases))
            if len(page) < remaining:
                break
            offset += remaining
        return rows

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> int:
        statement = bind_named(sql, params or {})
        head = statement.lstrip().split(None, 1)[0].upper()
        if head == "INSERT":
            upsert = _parse_upsert(statement)
            if upsert is not None:
                return self._upsert(upsert)
            replace = _parse_insert_or_replace(statement)
            if replace is not None:
                return self._upsert(replace)
            table, values = _parse_insert(statement)
            return len(self._insert_rows(table, [values]))
        if head in {"UPDATE", "DELETE"}:
            # ``_zcql`` already extracts whatever rows ZCQL returns for the
            # statement; for UPDATE/DELETE that is the affected rows, not a
            # separate count field. Reporting 0 unconditionally (as this used
            # to) silently misled every caller that logs or acts on a rowcount,
            # e.g. purge_expired() always claiming nothing was purged.
            return len(self._zcql(statement))
        raise CIPError(f"Unsupported statement for the Catalyst adapter: {head}", sql=statement[:120])

    def _upsert(self, plan: "UpsertPlan") -> int:
        """Emulate ``INSERT … ON CONFLICT`` with read-then-write.

        ZCQL has no upsert. Doing this in the adapter rather than in ten
        repositories keeps one dialect difference in one place, and keeps the
        SQLite path on its native atomic statement.

        This is *not* atomic: a concurrent writer between the SELECT and the
        write can produce a duplicate. Every caller of this path is a pipeline
        stage keyed on a deterministic natural key and designed to be replayed
        (implementationv2 §5.2), so a re-run converges. It must not be used for
        a counter or anything else where lost updates matter.
        """
        where = " AND ".join(
            f"{column} = {quote_literal(plan.values.get(column))}" for column in plan.conflict_columns
        )
        existing = self._zcql(f"SELECT ROWID FROM {plan.table} WHERE {where} LIMIT 0, 1")
        if not existing:
            return len(self._insert_rows(plan.table, [plan.values]))
        if not plan.updates:  # DO NOTHING
            return 0
        assignments = ", ".join(
            f"{column} = {quote_literal(value)}" for column, value in plan.resolved_updates().items()
        )
        self._zcql(f"UPDATE {plan.table} SET {assignments} WHERE {where}")
        return 1

    def execute_many(self, sql: str, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        head = sql.lstrip().split(None, 1)[0].upper()
        if head != "INSERT":
            total = 0
            for row in rows:
                total += self.execute(sql, row)
            return total
        table, columns = _parse_insert_columns(sql)
        payload = [{column: row.get(column) for column in columns} for row in rows]
        written = 0
        for chunk in _chunks(payload, 200):
            written += len(self._insert_rows(table, chunk))
        return written

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """No-op context manager.

        The Data Store has no multi-row transaction primitive. Pretending
        otherwise would be worse than saying so: callers that need atomicity
        must be designed to be idempotent and replayable, which the loader and
        the intelligence refresh already are.
        """
        yield

    def close(self) -> None:  # pragma: no cover - nothing to release
        return None

    def table_columns(self, table: str) -> list[str]:
        """Declared columns for ``table``, from the static schema manifest.

        Catalyst has no documented live introspection endpoint equivalent to
        SQLite's ``PRAGMA table_info``, so this reads the same
        ``schema.sql`` + ``migrations.py`` this package ships (see
        ``infrastructure.db.schema_reflection``) — accurate as long as the
        live Catalyst project was actually provisioned to match those files.
        That is a real limitation, not a fallback pretending to be equivalent
        to live introspection; see ``docs/deployment/catalyst-schema.md``.
        """
        from ..db.schema_reflection import schema_columns

        return list(schema_columns().get(table, []))

    # ---------------------------------------------------------------- HTTP
    def _zcql(self, statement: str) -> list[dict[str, Any]]:
        body = json.dumps({"query": statement}).encode("utf-8")
        payload = self._call("POST", "/query", body)
        rows: list[dict[str, Any]] = []
        for entry in payload.get("data", []) or []:
            flattened: dict[str, Any] = {}
            for value in entry.values():
                if isinstance(value, dict):
                    flattened.update(value)
            rows.append(flattened or entry)
        return rows

    def _insert_rows(self, table: str, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        body = json.dumps([dict(row) for row in rows]).encode("utf-8")
        payload = self._call("POST", f"/table/{table}/row", body)
        return payload.get("data", []) or []

    def _call(self, method: str, path: str, body: bytes) -> dict[str, Any]:
        url = f"{self._base}{path}"
        request = urllib_request.Request(url, data=body, method=method)
        request.add_header("Authorization", f"Zoho-oauthtoken {self._auth.token()}")
        request.add_header("Content-Type", "application/json")
        request.add_header("ENVIRONMENT", self._settings.catalyst_environment)
        try:
            with urllib_request.urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib_error.HTTPError as exc:  # pragma: no cover - network path
            body = exc.read().decode("utf-8", errors="replace")[:400]
            LOGGER.error("catalyst_http_error",
                         extra={"status": exc.code, "path": path, "body": body})
            # `detail` is CIPError's first positional parameter, so passing it
            # as a keyword here collided and raised TypeError -- which threw
            # away the upstream response and made every Catalyst HTTP failure
            # unreadable. The response body goes in as `response_body`.
            raise ProviderError(f"Catalyst request failed: {body}", provider="catalyst",
                                status=exc.code, path=path, response_body=body) from exc
        except urllib_error.URLError as exc:  # pragma: no cover - network path
            raise ProviderError("Catalyst is unreachable", provider="catalyst") from exc


# ------------------------------------------------------------- SQL parsing


@dataclass(slots=True)
class UpsertPlan:
    """A parsed ``INSERT … ON CONFLICT`` ready to run as read-then-write."""

    table: str
    values: dict[str, Any]
    conflict_columns: list[str]
    #: Column -> either a literal value or the sentinel ``EXCLUDED`` marker.
    updates: dict[str, Any]

    def resolved_updates(self) -> dict[str, Any]:
        """Resolve ``excluded.col`` references against the incoming row."""
        resolved: dict[str, Any] = {}
        for column, value in self.updates.items():
            if isinstance(value, _Excluded):
                if value.column not in self.values:
                    raise CIPError(
                        "ON CONFLICT update references an excluded column that is not in the INSERT",
                        column=value.column,
                    )
                resolved[column] = self.values[value.column]
            else:
                resolved[column] = value
        return resolved


@dataclass(frozen=True, slots=True)
class _Excluded:
    """Marker for an ``excluded.<column>`` reference in a DO UPDATE SET."""

    column: str


def _find_top_level(statement: str, needle: str) -> int:
    """Index of ``needle`` (case-insensitive) outside any string literal."""
    upper = statement.upper()
    target = needle.upper()
    inside = False
    index = 0
    while index < len(statement):
        char = statement[index]
        if char == "\\" and inside:
            index += 2
            continue
        if char == "'":
            inside = not inside
        elif not inside and upper.startswith(target, index):
            return index
        index += 1
    return -1


def _parse_upsert(statement: str) -> UpsertPlan | None:
    """Parse ``INSERT … ON CONFLICT (…) DO {NOTHING|UPDATE SET …}``.

    Returns ``None`` for a plain INSERT so the caller keeps the fast path.
    """
    marker = _find_top_level(statement, " ON CONFLICT")
    if marker < 0:
        return None

    insert_part = statement[:marker]
    table, values = _parse_insert(insert_part)

    remainder = statement[marker:].lstrip()[len("ON CONFLICT"):].lstrip()

    if not remainder.startswith("("):
        raise CIPError("ON CONFLICT requires an explicit column list", sql=statement[:160])
    close = remainder.index(")")
    conflict_columns = [part.strip().strip('"') for part in remainder[1:close].split(",") if part.strip()]
    if not conflict_columns:
        raise CIPError("ON CONFLICT column list is empty", sql=statement[:160])

    action = remainder[close + 1:].strip()
    upper_action = action.upper()
    if upper_action.startswith("DO NOTHING"):
        return UpsertPlan(table=table, values=values, conflict_columns=conflict_columns, updates={})
    if not upper_action.startswith("DO UPDATE SET"):
        raise CIPError("Unsupported ON CONFLICT action", sql=statement[:160])

    set_clause = action[len("DO UPDATE SET"):].strip()
    updates: dict[str, Any] = {}
    for assignment in _split_top_level(set_clause):
        if "=" not in assignment:
            raise CIPError("Malformed ON CONFLICT assignment", assignment=assignment[:80])
        column, _, raw = assignment.partition("=")
        column = column.strip().strip('"')
        raw = raw.strip()
        if raw.lower().startswith("excluded."):
            updates[column] = _Excluded(raw.split(".", 1)[1].strip().strip('"'))
        else:
            updates[column] = _literal_to_python(raw)
    if not updates:
        raise CIPError("ON CONFLICT DO UPDATE has no assignments", sql=statement[:160])
    return UpsertPlan(table=table, values=values, conflict_columns=conflict_columns, updates=updates)


def _parse_insert_or_replace(statement: str) -> "UpsertPlan | None":
    """Turn SQLite's ``INSERT OR REPLACE`` into the upsert plan ZCQL needs.

    Eleven repositories write with ``INSERT OR REPLACE INTO`` -- the pipeline's
    idempotent-replay idiom. ZCQL has no such statement, and the plain INSERT
    parser rejected it outright ("Not an INSERT statement"), which is what
    stopped a conversation turn from ever being saved.

    "Replace the row with this primary key" is exactly an upsert keyed on the
    primary key, so it reuses ``_upsert`` rather than adding a second
    read-then-write path -- including that method's non-atomicity caveat.
    """
    if not re.match(r"\s*INSERT\s+OR\s+REPLACE\s+INTO\s", statement, re.IGNORECASE):
        return None

    normalised = re.sub(r"\s*INSERT\s+OR\s+REPLACE\s+INTO\s", "INSERT INTO ",
                        statement, count=1, flags=re.IGNORECASE)
    table, values = _parse_insert(normalised)

    from ..db.schema_reflection import schema_primary_keys

    conflict_columns = [c for c in schema_primary_keys().get(table, []) if c in values]
    if not conflict_columns:
        # Without a key there is nothing to replace *on*; inserting blindly
        # would duplicate silently, so refuse rather than corrupt the table.
        raise CIPError(
            f"INSERT OR REPLACE on '{table}' has no primary-key column to match on",
            sql=statement[:160],
        )
    return UpsertPlan(
        table=table,
        values=values,
        conflict_columns=conflict_columns,
        updates={k: v for k, v in values.items() if k not in conflict_columns},
    )


def _parse_insert(statement: str) -> tuple[str, dict[str, Any]]:
    table, columns = _parse_insert_columns(statement)
    values_part = statement[statement.upper().index("VALUES") + 6:].strip()
    values_part = values_part.strip("()")
    values = _split_top_level(values_part)
    if len(values) != len(columns):
        raise CIPError("INSERT column/value count mismatch", sql=statement[:160])
    return table, {column: _literal_to_python(value) for column, value in zip(columns, values)}


def _parse_insert_columns(statement: str) -> tuple[str, list[str]]:
    upper = statement.upper()
    if "INSERT INTO" not in upper:
        raise CIPError("Not an INSERT statement", sql=statement[:120])
    after = statement[upper.index("INSERT INTO") + 11:].lstrip()
    open_paren = after.index("(")
    table = after[:open_paren].strip().strip('"')
    close_paren = after.index(")")
    columns = [part.strip().strip('"') for part in after[open_paren + 1:close_paren].split(",")]
    return table, columns


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    inside = False
    current: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and inside:
            current.append(text[index:index + 2])
            index += 2
            continue
        if char == "'":
            inside = not inside
        elif not inside and char == "(":
            depth += 1
        elif not inside and char == ")":
            depth -= 1
        elif not inside and char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if current:
        parts.append("".join(current).strip())
    return parts


def _literal_to_python(literal: str) -> Any:
    text = literal.strip()
    if text.upper() == "NULL":
        return None
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1].replace("\\'", "'").replace("\\\\", "\\")
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _chunks(rows: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(rows), size):
        yield rows[start:start + size]
