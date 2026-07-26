# Phase 2 report — Catalyst Data Store, Stratus and replay-safe pipeline

Companion to `implementationv2-phases-0-2.md` Phase 2. This phase is the
least completable without a live Catalyst project: several of its own exit
criteria are explicitly "run this against Catalyst Development and compare
with SQLite," which cannot be done from here. What follows is split honestly
between what is built and locally verified, and what is designed but requires
a live project to actually exercise.

## P2-01 — Schema provisioning outside application startup

Done. `scripts/generate_schema_manifest.py` parses `schema.sql` +
`migrations.py` into `docs/deployment/catalyst-schema-manifest.json` (47
tables, 46 indexes) — table, column, type, `NOT NULL`, primary key, and
foreign-key reference per column. Process for applying it documented in
[catalyst-schema.md](catalyst-schema.md).

`ctl_schema_version` already exists in the local schema and is populated by
`apply_migrations()`; the manifest includes it like any other table so a
Catalyst provisioning pass creates it too. **Not done:** an automated
readiness check that queries `ctl_schema_version` on Catalyst and refuses to
serve traffic on a version mismatch — `deployment_problems()` checks
*configuration*, not *live schema version*, and adding the latter needs a
live project to test the failure path against.

## P2-02 — Portable schema-capability port

Done, and it found a real bug in itself during development. `DataStore` gained
`table_columns(table) -> list[str]`; `SQLiteDataStore` still uses
`PRAGMA table_info` directly (cheap, authoritative, no reason to abstract it
away locally); `CatalystDataStore.table_columns()` parses the same
`schema.sql` + `migrations.py` via the new
`infrastructure/db/schema_reflection.py`. `loader.py`'s `_table_columns` now
calls the port instead of constructing `PRAGMA table_info("...")` — the one
and only place in the application layer that ever did.

**The bug this found:** the first version of `schema_reflection.py` parsed
only `schema.sql`, and a parity test against a live SQLite database (every
table, `PRAGMA table_info` vs. the parser) caught that it missed
`cip_user_account.external_subject` — added by migration 2's
`ALTER TABLE ... ADD COLUMN`, not present in the base file. Fixed by folding
every `MIGRATIONS` entry into the same parse pass (both `CREATE TABLE` and
`ALTER TABLE ... ADD COLUMN` forms). Re-run: 47/47 tables match, including the
migration-only `cip_event_calendar` table. This regression is now pinned by
`test_schema_reflection_parity.py`, which would fail again if a future
migration were added without the parser picking it up.

## P2-03 — ZCQL-incompatible SQL inventory

Mostly done in the prior Catalyst-adapters pass, carried forward here:

- `ON CONFLICT` (10 sites across the loader, KV store, and intel/platform
  repositories): translated by the adapter into read-then-write, documented
  as non-atomic-but-replay-safe. Unchanged this phase.
- `PRAGMA`: the one application-layer site (`loader.py`) now goes through
  `table_columns()`; `CatalystDataStore.query()` still refuses any raw
  `PRAGMA` that reaches it, as a safety net.
- `executescript`: used only by `apply_migrations()` against
  `SQLiteDataStore`; `CatalystDataStore` has no such method, so a caller that
  tried would fail at the type level, not silently succeed against the wrong
  backend.
- Pagination: `CatalystDataStore.query()` already paginated by appending
  `LIMIT offset, PAGE_SIZE` server-side and looping until a short page — this
  predates this phase and was not touched.
- Parameter binding: `quote_literal`/`bind_named` already had contract tests
  for quotes, Unicode, and injection-shaped strings (prior pass); unchanged.
- **Not inventoried this phase:** a fresh, exhaustive static grep for any
  *new* unsupported pattern introduced since the prior pass. The seasonality,
  event-comparison, and sociology-suppression SQL added in that pass all use
  plain parameterized `SELECT ... GROUP BY`, which is already covered by
  existing `AnalyticsRepository` contract exercise; no new `ON CONFLICT` or
  `PRAGMA` was introduced by that work.

## P2-04 — Harden `CatalystDataStore`

Unchanged this phase beyond P2-02's addition. Existing from the prior pass:
scalar escaping in one place (`quote_literal`), provider-error-to-typed-error
conversion, structured logging without row values. **Not done:** a table/column
allow-list where the adapter builds identifiers (the adapter today trusts
`bind_named`'s parameterization for values but does not separately allow-list
table/column names used in f-string-built SQL like
`f"SELECT COUNT(*) AS n FROM {table}"` in `test_deployment_smoke.py`/health
checks — those call sites are test/ops code, not user-input-driven, but the
adapter itself does not enforce this at the port boundary). Flagged as an
open hardening item, not fixed this phase.

## P2-05 — Stratus FileStore

`StratusFileStore` (prior pass) already implements the full interface with
key validation (rejects `..`, backslash-unsafe characters, control
characters) and routes downloads through the existing `authorize_file_access`
policy rather than returning a public URL. **Not done this phase:** the live
round-trip, checksum-mismatch, and unauthorized-owner tests against a real
Stratus bucket — `test_deployment_smoke.py`'s `TestStratusRoundTrip` class
covers exactly this, gated on live credentials, and remains skipped here.

## P2-06/P2-07 — Durable run control and publication gating

**Partially done, and this is the most important gap to be honest about.**

What exists: `cip_refresh/main.py`'s `run_stage()` (Phase 1 work) registers a
`ctl_batch_log` row before a stage runs and marks it `FAILED`/`LOADED`
afterward, with a correlation id and duration. This gives per-invocation
run-status durability for the refresh function specifically.

What does **not** exist: the active-publication-version state machine P2-07
describes — curated/derived rows tagged with a `run_id`/`publication_version`,
and an atomic "promote" step so a DQ failure genuinely cannot expose new
intelligence, with API reads filtered to the active version. Today, the
existing `DataQualitySuite` blocks a *load* from proceeding to intelligence
refresh on a blocking failure (verified by the existing
`TestPlantedSignalsAreDetected`-adjacent pipeline tests), which is real
protection — but it is a **gate before writing**, not a **versioned,
promotable publication** the way P2-07 specifies. The distinction matters at
Catalyst scale: without a live project's Data Store to test an atomic
version-pointer update against, building that state machine now would be
architecture-by-assumption. This is the single largest piece of Phase 2 left
for a session with real Catalyst access.

## P2-08 — Four refresh stages Catalyst-safe

The four stage names and semantics (`ingest`, `data_quality`, `intelligence`,
`retention`) are unchanged; `run_stage()`'s new wrapper (P1-06/P2-06 overlap)
applies uniformly to all four. Not independently re-verified against Catalyst
this phase beyond the local dry-runs described in
`phase1-catalyst-runtime.md`.

## P2-09 — SQLite-vs-Catalyst comparison

**Not done.** Requires a live Catalyst Development project to load the same
seed into both backends and diff row/edge/alert/embedding counts and
evidence-locator sets. Nothing here can substitute for that; it is listed as
fully open.

## P2-10 — Failure/retry/rollback injection

**Not done** for the same reason as P2-09 — most of the listed failure modes
(provider timeout, Stratus export read failure, live DQ-then-retry) require a
live project. What *is* covered locally: the loader's idempotent upsert
behavior under `ON CONFLICT` replay (prior pass's contract tests), and
`cip_refresh`'s invalid-stage-name rejection (this phase, verified directly).

## P2-11 — Export ownership and recovery

Export ownership enforcement (`authorize_file_access`, owner-prefixed export
keys) is existing, tested application logic
(`test_file_access.py`, `TestExport` in `test_api.py`), unchanged this phase.
**Not done:** the live Stratus export/download/restore cycle — needs a
project.

## Exit gate

- [x] Catalyst Data Store schema is provisioned *as a manifest*; live
      version-checking against a running project is not built.
- [ ] Catalyst Stratus landing/export paths are live and access-controlled —
      access-controlled: yes (existing policy); live: not exercised.
- [x] All shared metadata operations are portable; no `PRAGMA` reaches
      Catalyst from the application layer (`executescript` remains
      SQLite-only by type, never called against `CatalystDataStore`).
- [x] The `ON CONFLICT` upsert pattern is fixed and contract-tested (prior
      pass, unchanged here).
- [~] Batch/run state and correlation IDs are durable for `cip_refresh`
      invocations; **publication-version state and a DQ-gated active-version
      pointer are not built** — see P2-06/07 above.
- [x] DQ failure cannot publish new intelligence *in the sense that a
      blocking failure stops the refresh pipeline before it runs* — the
      stronger "old version stays queryable while a new one is validated"
      guarantee is not built.
- [x] Replaying the same `ON CONFLICT` upsert is safe (prior pass).
- [ ] SQLite and Catalyst parity corpus — not run; needs a live project.
- [x] API agents still receive the same domain repositories/ports; no
      Catalyst SDK import appears outside `infrastructure/catalyst/` and
      `interface/container.py`'s factories (grep-verified).
- [ ] Export ownership/backup/restore against live Stratus — not exercised.
- [ ] Rollback to a previous active publication — no publication-version
      mechanism exists yet to roll back to.

**Exit artifact:** this document, `catalyst-schema-manifest.json`,
`catalyst-schema.md`, and the test suite additions
(`test_schema_reflection.py`, `test_schema_reflection_parity.py`,
`test_schema_manifest_generator.py`, the two new `TestSchemaCapability` cases
in `test_catalyst_contract.py`).

**Honest summary for whoever picks this up next:** Phase 2's data-portability
work (schema capability, upsert translation, manifest generation) is done and
tested to the limit of what's possible without a live project. Its
*operational* half — versioned publication, live parity, failure injection,
export recovery — is a distinct, larger piece of work that needs a
provisioned Catalyst Development project as a prerequisite, not more time at
a keyboard without one.
