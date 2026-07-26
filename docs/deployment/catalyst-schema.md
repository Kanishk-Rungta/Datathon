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

1. Create the tables and columns. Preferred:

   ```bash
   node scripts/provision_catalyst_datastore.js --dry-run   # print the plan
   node scripts/provision_catalyst_datastore.js             # apply it
   ```

   It is idempotent — existing tables/columns are skipped and never altered,
   so a partial run is simply re-run. It requires `catalyst login` and a
   `.catalystrc` in the repo root, and provisions whichever project that file
   points at.

   Fallback, if the undocumented API contract ever changes and the script's
   discovery step fails: `python scripts/generate_catalyst_console_checklist.py`
   produces `docs/deployment/catalyst-schema-console-checklist.md`, the same
   schema as a manual console checklist. Both encode identical
   type/uniqueness adaptations, so neither needs a per-column judgment call.
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

- There is **no CLI command** for creating tables or columns, and
  `iac:import` can only create a *new* project, never inject schema into an
  already-linked one. The CLI's own Data Store client
  (`zcatalyst-cli/lib/endpoints/lib/datastore.js`) exposes only `GET`.
- **But the admin REST API does support it.** The CLI's OAuth scopes include
  `ZohoCatalyst.tables.ALL` and `ZohoCatalyst.tables.columns.ALL` — `ALL`,
  not `READ` — and `POST /baas/v1/project/{id}/table` with
  `{"table_name": "..."}` was confirmed live to return 200, with `DELETE
  /table/{table_id}` cleaning up after it. So "console-only" is true of the
  *CLI and documentation*, not of the platform. This is what
  `scripts/provision_catalyst_datastore.js` uses.
  The endpoints are undocumented, so that script discovers the accepted
  column payload against a throwaway table before touching real ones rather
  than hard-coding a guessed contract.
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
- **ZCQL cannot join on natural keys, and foreign keys do not fix it.** This
  is the finding that decides whether the application can run on Catalyst
  Data Store at all, so it is recorded in full:

  ```text
  SELECT ... FROM curated_Unit u LEFT JOIN curated_District d
    ON d.DistrictID = u.DistrictID
  -> 400  ZCQL QUERY ERROR: "No relationship between tables d and u"
  ```

  ZCQL only joins tables that have a declared foreign-key relationship. But a
  Catalyst foreign key references the parent's **`ROWID`** — the 17-digit id
  Catalyst generates — not the parent's business key. Provisioning
  `curated_Unit.DistrictID` as a real Foreign Key column against
  `curated_District` was tried live, and the only accepted join was:

  ```text
  ON curated_District.ROWID = curated_Unit.DistrictID   -> 200, every DistrictName null
  ```

  Null on every row, because the column holds `DistrictID` values `1…31`,
  which are not ROWIDs. Making this work would mean loading parents first,
  capturing their generated ROWIDs, rewriting all 39 foreign-key values in
  child rows, and rewriting the application's 53 joins to join on ROWID —
  i.e. replacing the organiser's natural-key identity model, which evidence
  locators and the audit trail are built on.

  So the 39 foreign-key columns are deliberately left as plain `bigint`, and
  the relationships stay documentation of intent. Referential integrity is
  enforced where it already was: SQLite runs with `PRAGMA foreign_keys = ON`,
  and the pipeline's `orphan_case_reference` DQ check is a **BLOCKER** in
  both backends. A Catalyst-backed deployment must therefore either avoid
  multi-table joins in the repository layer or accept this limitation; it is
  not something further provisioning can resolve.

- `ctl_schema_version` **is** created, but imperatively by
  `migrations._ensure_version_table()` rather than in `schema.sql`, so it does
  not appear in the generated manifest and was not provisioned to Catalyst.
  Step 4 above therefore cannot be followed as written.
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
