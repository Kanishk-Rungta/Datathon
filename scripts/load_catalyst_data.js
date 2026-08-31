#!/usr/bin/env node
/*
 * Copy the seeded local SQLite dataset into the Catalyst Data Store.
 *
 * Why copy rather than re-generate
 * --------------------------------
 * Phase D2 asks that "the same synthetic batch produces the same
 * authoritative records and derived evidence" in both backends. Copying the
 * already-seeded SQLite database is the most direct way to satisfy that: the
 * curated rows *and* the derived intelligence (graph edges, embeddings,
 * entity-resolution links, hotspots, alerts, priorities) all come across
 * together, so the deployed app has the exact dataset the local test suite
 * asserts against -- not a separately generated one that merely resembles it.
 *
 * Rows are sent in batches because the row-by-row alternative is both slow
 * and the thing that triggered throttling during schema provisioning.
 *
 *   Prerequisites: `catalyst login`, `.catalystrc`, and a provisioned schema
 *   (run scripts/provision_catalyst_datastore.js first).
 *
 *   Usage:
 *     node scripts/load_catalyst_data.js --dry-run   # counts only, no writes
 *     node scripts/load_catalyst_data.js             # load
 *     node scripts/load_catalyst_data.js --verify    # compare row counts
 *     node scripts/load_catalyst_data.js --truncate  # clear target tables first
 *
 * Not idempotent by itself: Catalyst assigns its own ROWID per insert, so
 * re-running appends duplicates. Use --truncate for a clean reload.
 */

'use strict';

const fs = require('fs');
const path = require('path');
const { DatabaseSync } = require('node:sqlite');
const { REPO_ROOT, sleep, connect } = require('./lib/catalyst-api');

const DB_PATH = process.env.KSPCIP_SQLITE_PATH
  || path.join(REPO_ROOT, 'backend', 'var', 'ksp_cip.db');
const MANIFEST = path.join(REPO_ROOT, 'docs/deployment/catalyst-schema-manifest.json');

const DRY_RUN = process.argv.includes('--dry-run');
const VERIFY = process.argv.includes('--verify');
const TRUNCATE = process.argv.includes('--truncate');
const BATCH_SIZE = 100;

/**
 * Tables deliberately not copied.
 *
 * `cip_kv` holds session state, scratchpad and cache entries -- all
 * TTL-bounded and regenerable, and meaningless to move between environments.
 * `ctl_schema_version` tracks SQLite migration state, which says nothing
 * about the Catalyst schema and has no table there.
 */
const SKIP_TABLES = new Set(['cip_kv', 'ctl_schema_version']);

/** Catalyst-managed columns; never sent on insert. */
const SYSTEM_COLUMNS = new Set(['rowid', 'creatorid', 'createdtime', 'modifiedtime']);

function openDb() {
  if (!fs.existsSync(DB_PATH)) {
    throw new Error(
      `No SQLite database at ${DB_PATH}. Seed one first:\n` +
      '  python -m ksp_cip.cli seed'
    );
  }
  return new DatabaseSync(DB_PATH, { readOnly: true });
}

function sqliteTables(db) {
  return db.prepare(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
  ).all().map((r) => r.name);
}

/**
 * Coerce a SQLite value into something the JSON API accepts.
 * SQLite has no boolean type and node:sqlite surfaces INTEGER as BigInt,
 * which JSON.stringify refuses outright.
 */
function coerce(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === 'bigint') return Number(value);
  if (value instanceof Uint8Array) return Buffer.from(value).toString('base64');
  return value;
}

/**
 * Delete every row from a table.
 *
 * A single ZCQL `DELETE FROM t` only removes about one page (~200 rows) and
 * still reports success, so issuing it once silently leaves most of a large
 * table behind -- which then collides with the reload on any unique column
 * (`DUPLICATE_VALUE`). Repeat until the reported count reaches zero.
 *
 * The `/table/{id}/truncate` endpoint exists and does clear everything, but
 * it is asynchronous ("will be truncated shortly"), so it cannot be verified
 * before the reload begins. This loop is synchronous and self-verifying.
 */
async function clearTable(api, table, log) {
  let removed = 0;
  for (let pass = 0; pass < 200; pass++) {
    const res = await api.zcql(`DELETE FROM ${table}`);
    if (!res.ok) {
      log(`  ! ${table}: ${res.text.slice(0, 160)}`);
      return removed;
    }
    const first = (res.body.data || [])[0] || {};
    const inner = first[table] || Object.values(first)[0] || {};
    const n = Number(inner.DELETED_ROWS_COUNT ?? 0);
    removed += n;
    if (n === 0) return removed;
    await sleep(80);
  }
  log(`  ! ${table}: still deleting after 200 passes -- check for a runaway table`);
  return removed;
}

/**
 * Order tables so every parent loads before its children.
 *
 * Once the foreign keys are real relationships, Catalyst validates each
 * reference on insert -- a row pointing at a parent that is not there yet is
 * rejected outright. Alphabetical order breaks on the first such pair
 * (`curated_District` before `curated_State`), and the failure then cascades
 * to everything referencing the table that failed to load.
 *
 * Cycles are tolerated rather than fatal: a table that cannot be ordered is
 * appended, so a self-reference (`curated_Unit.ParentUnit`) degrades to a few
 * rejected rows instead of stopping the load.
 */
function dependencyOrder(manifest, skip) {
  const parents = new Map();
  for (const t of manifest.tables) {
    parents.set(t.table, new Set(
      t.columns.filter((c) => c.references && c.references.table !== t.table)
               .map((c) => c.references.table)));
  }

  const ordered = [];
  const placed = new Set();
  let progress = true;
  while (progress) {
    progress = false;
    for (const [table, deps] of parents) {
      if (placed.has(table)) continue;
      if ([...deps].every((d) => placed.has(d) || !parents.has(d))) {
        ordered.push(table);
        placed.add(table);
        progress = true;
      }
    }
  }
  for (const table of parents.keys()) {
    if (!placed.has(table)) ordered.push(table); // cyclic -- load last, best effort
  }
  return ordered.filter((t) => !skip.has(t));
}

async function main() {
  const log = (...a) => console.log(...a);
  const manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'));
  const planned = dependencyOrder(manifest, SKIP_TABLES);

  const db = openDb();
  const localTables = new Set(sqliteTables(db));

  log(`SQLite  : ${DB_PATH}`);
  log(`Tables  : ${planned.length} to consider\n`);

  if (DRY_RUN) {
    let total = 0;
    for (const t of planned) {
      if (!localTables.has(t)) { log(`  ${t.padEnd(32)} (absent locally)`); continue; }
      const n = Number(db.prepare(`SELECT COUNT(*) AS c FROM "${t}"`).get().c);
      total += n;
      if (n) log(`  ${t.padEnd(32)} ${String(n).padStart(7)}`);
    }
    log(`\nTotal rows to load: ${total.toLocaleString()}`);
    log('--dry-run: nothing was sent to Catalyst.');
    return;
  }

  const api = await connect(log);

  const list = await api.listTables();
  if (!list.ok) throw new Error(`Could not list tables: ${list.text.slice(0, 300)}`);
  const tableIds = new Map((list.body.data || []).map((t) => [t.table_name, t.table_id]));

  // ---------------------------------------------------------------- verify
  if (VERIFY) {
    log('===== ROW COUNT COMPARISON =====');
    let mismatched = 0;
    for (const t of planned) {
      const tid = tableIds.get(t);
      if (tid === undefined) { log(`  ${t.padEnd(32)} TABLE MISSING in Catalyst`); mismatched++; continue; }
      const local = localTables.has(t)
        ? Number(db.prepare(`SELECT COUNT(*) AS c FROM "${t}"`).get().c) : 0;
      const res = await api.zcql(`SELECT COUNT(ROWID) FROM ${t}`);
      let remote = -1;
      if (res.ok) {
        const first = (res.body.data || [])[0] || {};
        const inner = first[t] || Object.values(first)[0] || {};
        remote = Number(Object.values(inner)[0] ?? -1);
      }
      const same = local === remote;
      if (!same) mismatched++;
      log(`  ${t.padEnd(32)} local=${String(local).padStart(6)}  catalyst=${String(remote).padStart(6)}  ${same ? 'ok' : '<-- MISMATCH'}`);
      await sleep(60);
    }
    log(mismatched === 0
      ? '\nAll row counts match.'
      : `\n${mismatched} table(s) differ.`);
    process.exitCode = mismatched === 0 ? 0 : 1;
    return;
  }

  // -------------------------------------------------------------- truncate
  if (TRUNCATE) {
    // Reverse dependency order: clear children before the parents they point
    // at, so no delete has to null out a reference on the way through.
    log('Clearing target tables...');
    for (const t of [...planned].reverse()) {
      if (!tableIds.has(t)) continue;
      const removed = await clearTable(api, t, log);
      if (removed > 0) log(`  ${t.padEnd(32)} ${String(removed).padStart(6)} rows deleted`);
    }
    log('');
  }

  // ------------------------------------------------------------------ load
  let loaded = 0;
  const failures = [];

  for (const tableName of planned) {
    const tid = tableIds.get(tableName);
    if (tid === undefined) { failures.push(`${tableName}: table missing in Catalyst`); continue; }
    if (!localTables.has(tableName)) continue;

    const rows = db.prepare(`SELECT * FROM "${tableName}"`).all();
    if (rows.length === 0) continue;

    // Only send columns the Catalyst table actually has: a column renamed or
    // dropped on one side should be skipped loudly, never guessed at.
    const colRes = await api.listColumns(tid);
    const remoteCols = new Map(
      (colRes.body.data || [])
        .filter((c) => !SYSTEM_COLUMNS.has(c.column_name.toLowerCase()))
        .map((c) => [c.column_name.toLowerCase(), c.column_name])
    );

    const localCols = Object.keys(rows[0]);
    const usable = localCols.filter((c) => remoteCols.has(c.toLowerCase()));
    const dropped = localCols.filter((c) => !remoteCols.has(c.toLowerCase()));
    if (dropped.length) {
      log(`  ! ${tableName}: no Catalyst column for ${dropped.join(', ')} -- not sent`);
    }

    let done = 0;
    for (let i = 0; i < rows.length; i += BATCH_SIZE) {
      const batch = rows.slice(i, i + BATCH_SIZE).map((r) => {
        const out = {};
        for (const c of usable) out[remoteCols.get(c.toLowerCase())] = coerce(r[c]);
        return out;
      });
      const res = await api.insertRows(tid, batch);
      if (res.ok) {
        done += batch.length;
      } else {
        failures.push(`${tableName} rows ${i}-${i + batch.length - 1}: ${res.text.slice(0, 200)}`);
      }
      await sleep(120);
    }
    loaded += done;
    log(`  ${tableName.padEnd(32)} ${String(done).padStart(6)}/${String(rows.length).padStart(6)} rows`);
  }

  log(`\nRows loaded: ${loaded.toLocaleString()}`);
  if (failures.length) {
    log(`\n${failures.length} FAILURE(S):`);
    for (const f of failures.slice(0, 20)) log('  - ' + f);
    if (failures.length > 20) log(`  ... and ${failures.length - 20} more`);
    process.exitCode = 1;
  }
  log('\nRun with --verify to compare row counts against SQLite.');
}

main().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
