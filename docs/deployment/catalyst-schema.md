# Catalyst Data Store provisioning (P2-01)

## The rule

`schema.sql` is the **semantic source** of required tables and columns, never
a script sent to ZCQL directly. `CatalystDataStore` has no `executescript`
method — it refuses `PRAGMA` and has no bulk-DDL execution path at all, on
purpose, so that "how do I create the tables" cannot become "call SQLite's
migration code against Catalyst and hope."

## The manifest

`docs/deployment/catalyst-schema-manifest.json` is generated, never hand
written:

```bash
python scripts/generate_schema_manifest.py
```

It parses `backend/ksp_cip/infrastructure/db/schema.sql` plus every entry in
`infrastructure/db/migrations.py` and lists, per table: column name, declared
type, `NOT NULL`, primary key, and foreign-key reference; separately, every
`CREATE INDEX`. 47 tables, 46 indexes at the time of this pass.

Regenerate it whenever `schema.sql` or `migrations.py` changes, and diff the
output — a table or column appearing/disappearing in that diff is exactly
what a reviewer should be looking for before approving a provisioning change.

**This manifest is checked against the same file `CatalystDataStore.table_columns()`
reads at runtime** (`infrastructure/db/schema_reflection.py`), and a test
(`test_schema_manifest_generator.py`) asserts the two agree — so the
provisioning manifest and the runtime schema-capability port cannot silently
drift apart from each other, only from a live Catalyst project that was
provisioned differently than what's on disk.

## Applying it

1. For each table in the manifest, create the equivalent Catalyst Data Store
   table **through the console** — this is confirmed to be the only path (see
   below). Run `python scripts/generate_catalyst_console_checklist.py` and
   follow `docs/deployment/catalyst-schema-console-checklist.md` column by
   column; it already encodes the type/uniqueness adaptations below so no
   per-column judgment call is needed at provisioning time.
2. Foreign keys in this manifest are **documentation of intent**, not a Data
   Store constraint to necessarily enforce identically — Catalyst's own
   constraint model may differ; enforce referential integrity the way the
   target project's Data Store actually supports, and note where it diverges.
3. ~~Create the indexes listed, matching `unique: true` where set.~~ Not
   possible — see "None of this schema's 46 planned indexes are creatable"
   below. Skip this step; it is kept here struck through so a reader doesn't
   wonder whether it was forgotten.
4. ~~Create `ctl_schema_version` first...~~ Not possible as written — that
   table does not exist in `schema.sql`. See below.
5. Record the exact console steps used, in this file's git history (a new
   commit updating this section), so the next person provisioning a project
   has the actual sequence, not just the target shape.

## What this does not do

- It does not create anything. No network call, no credentials, no live
  project touched by generating or reading the manifest.
- It does not replace a reviewed provisioning step with an automatic one —
  Phase 2's own rule (P2-01) is that Catalyst schema changes are "applied
  through reviewed provisioning steps," explicitly not `executescript()` from
  a request or function.
- It does not capture check constraints, generated columns, or trigger logic
  — none exist in `schema.sql` today, so there was nothing to lose in keeping
  the parser this size; if one is added, extend the parser and this document
  together, and add a fixture test for it the same way the existing tests
  pin primary-key and foreign-key extraction.

## Verified vs. assumed

**Verified:** the manifest generator agrees with the runtime schema-capability
port on every table and column, for the schema as it exists on disk right now
(automated test).

**Verified against the live KSP-CIP Development project (console UI,
Zoho's own documentation, and the CLI's own source):**

- Table and column creation is **console-UI-only**. There is no REST API, no
  CLI command, and no IaC-into-an-existing-project path — the CLI's own
  Data Store client (`zcatalyst-cli/lib/endpoints/lib/datastore.js`) only
  exposes `GET` (list tables, get columns), never a create. `iac:export`/
  `iac:import` round-trip whole-project clones, not schema injection into an
  already-linked project.
- Column types actually offered: `Text`, `Var Char`, `Date`, `DateTime`,
  `Int`, `Double`, `Boolean`, `Bigint`, `Foreign Key`, `Encrypted Text`.
  `schema.sql`'s three SQLite types map as: `TEXT` → `Text` (or `Var Char`
  when uniqueness is needed — see below), `INTEGER` → `Bigint`, `REAL` →
  `Double`.
- **`Var Char` is capped at 255 characters** (Catalyst's own stated limit).
  `Text` cannot be constrained `Is Unique` at all — only `Var Char` and the
  numeric types expose an `Is Unique` toggle. This means a `TEXT PRIMARY KEY`
  column must be provisioned as `Var Char(255)`, not `Text`, to get any
  DB-level uniqueness — a genuine type change from the source schema, not
  just a rename.
- **`Is Unique` is single-column only.** Catalyst has no composite/multi-column
  uniqueness constraint. The 6 tables in this schema with a composite primary
  key (`cip_conversation_turn`, `cip_kv`, `cip_unit_closure`, `ctl_row_hash`,
  `curated_CrimeHeadActSection`, `curated_Section`) get `Is Mandatory` on every
  key column and no DB-enforced uniqueness — the combination's uniqueness is
  an application-level guarantee only.
- **None of this schema's 46 planned indexes are creatable in Catalyst Data
  Store**, full stop — not a limit, an absent feature. Catalyst's "Search
  Index" toggle (available on `Var Char`/numeric columns) is full-text search
  integration with Catalyst Search, unrelated to query-performance indexing;
  secondary/composite indexes exist only in **Catalyst NoSQL**, a different
  service this application does not use for curated data. The one exception:
  `ux_case_crimeno` (`curated_CaseMaster.CrimeNo`) is a single-column unique
  index, so it is expressible as `Is Unique` on that one `Var Char` column.
  This is a hard platform gap, not an open item — any query that depended on
  one of the other 45 indexes for performance will do a full scan on Catalyst.
- `ctl_schema_version`, referenced in step 4 above, **does not exist** in
  `schema.sql` or the generated manifest. It was never implemented. Either add
  it to the real schema before relying on this step, or drop the step.
- Every new table arrives with 4 system columns already present: `ROWID`
  (bigint, the actual storage primary key), `CREATORID` (bigint),
  `CREATEDTIME` (datetime), `MODIFIEDTIME` (datetime). Application primary
  keys never become the real storage key — Catalyst's `ROWID` always is.

`scripts/generate_catalyst_console_checklist.py` turns the manifest into
`docs/deployment/catalyst-schema-console-checklist.md`, an ordered,
copy-into-the-console checklist encoding all of the above per column, since
there is no faster or programmatic path available.

**Still assumed, not verified:** `Foreign Key` as a first-class column type
was seen in the dropdown but not exercised — this schema does not use it,
since the manifest's foreign keys are documentation of intent, not enforced
constraints (see above). `Boolean`, `Date`, `Encrypted Text`, and `PII/ePHI`
tagging were seen in the console but are not used by any column in this
schema; a follow-up pass could apply `PII/ePHI` to genuinely
person-identifying columns.
