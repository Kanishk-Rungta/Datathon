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
   table through the Catalyst console, CLI, or an IaC workflow — column
   names and types from the manifest, `primary_key: true` as the table's ID
   column, `not_null: true` columns as required.
2. Foreign keys in this manifest are **documentation of intent**, not a Data
   Store constraint to necessarily enforce identically — Catalyst's own
   constraint model may differ; enforce referential integrity the way the
   target project's Data Store actually supports, and note where it diverges.
3. Create the indexes listed, matching `unique: true` where set.
4. Create `ctl_schema_version` first (it is in the manifest like any other
   table) and insert one row recording the schema version this manifest was
   generated at, so a mismatched deployment can refuse to start rather than
   run against an unprovisioned or stale project — this is the "read-only
   startup check" P2-01 asks for; it is not implemented as an automatic
   migration runner against Catalyst, only as a compatibility check.
5. Record the exact console/CLI steps or exported IaC definition used, in
   this file's git history (a new commit updating this section), so the next
   person provisioning a project has the actual commands, not just the
   target shape.

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

**Assumed, not verified:** that Catalyst Data Store's actual type system,
constraint model, and index semantics map cleanly onto what's declared here.
No live Catalyst project has been used to provision this schema. The first
real provisioning pass against a Development project should update this
document with whatever translation was actually needed — a type that doesn't
exist, an index limit, a constraint Catalyst doesn't support — since that is
exactly the kind of gap a manifest generated from SQLite's own schema dialect
cannot predict in advance.
