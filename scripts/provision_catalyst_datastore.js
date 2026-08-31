#!/usr/bin/env node
/*
 * Provision the Catalyst Data Store schema for KSP-CIP.
 *
 * Why this exists
 * ---------------
 * Catalyst exposes no CLI command and no documented REST endpoint for creating
 * Data Store tables, and `iac:import` can only create a *new* project. The
 * Catalyst console can do it, but this schema is 47 tables / 351 columns --
 * roughly 2,000 individual UI interactions.
 *
 * The admin API *does* support it: the CLI's own OAuth scopes include
 * `ZohoCatalyst.tables.ALL` and `ZohoCatalyst.tables.columns.ALL` (ALL, not
 * READ). This script uses those endpoints directly, authenticating with the
 * CLI's existing login -- so it works only for a project the operator running
 * it is already logged in to and authorised for.
 *
 * Because the endpoints are undocumented, the column request shape is
 * *discovered* at runtime against a throwaway table rather than hard-coded
 * from a guess (see discoverColumnShape). If Zoho changes the contract, this
 * fails loudly on a scratch table instead of half-provisioning the real one.
 *
 *   Prerequisites: `catalyst login`, and a `.catalystrc` in the repo root.
 *   Usage:
 *     node scripts/provision_catalyst_datastore.js --dry-run
 *     node scripts/provision_catalyst_datastore.js
 *
 * Idempotent: existing tables and columns are left alone, so a partial run can
 * simply be re-run. It never drops or alters an existing column.
 */

'use strict';

const fs = require('fs');
const path = require('path');
const { REPO_ROOT, sleep, connect, readProject } = require('./lib/catalyst-api');

const MANIFEST = path.join(REPO_ROOT, 'docs/deployment/catalyst-schema-manifest.json');
const PROBE_TABLE = 'zz_provision_probe';

const DRY_RUN = process.argv.includes('--dry-run');
const VERIFY_ONLY = process.argv.includes('--verify');
const FOREIGN_KEYS = process.argv.includes('--foreign-keys');

//: The only two Catalyst accepts. RESTRICT and NO-ACTION are rejected
//: (PATTERN_NOT_MATCHED), so the choice is between nulling the reference and
//: cascading the delete. Cascading would let removing one district delete its
//: police units -- and through them, case references -- so it is never used.
const ON_DELETE = 'ON-DELETE-SET-NULL';

/**
 * Convert the declared foreign-key columns into real Catalyst relationships.
 *
 * ZCQL only joins tables that have a declared relationship, so without this
 * every join in the application fails with "No relationship between tables".
 *
 * The subtlety that matters: a Catalyst foreign key can target **any unique
 * column**, not just the parent's ROWID. The console's dialog only asks for a
 * parent *table* and silently targets ROWID, which is why a console-created
 * key appears to make natural-key joins impossible. Targeting the parent's
 * business key instead -- `curated_District.DistrictID`, exactly as the
 * organiser's ER diagram specifies -- makes `ON d.DistrictID = u.DistrictID`
 * work, with no change to the schema or to any query.
 *
 * Destructive: a column's type cannot be changed in place, so each one is
 * dropped and recreated, losing its values. Reload afterwards with
 * `node scripts/load_catalyst_data.js --truncate`.
 */
async function provisionForeignKeys(api, manifest, log) {
  const list = await api.listTables();
  const tableIds = new Map((list.body.data || []).map((t) => [t.table_name, t.table_id]));
  const columnCache = new Map();
  const columnsOf = async (name) => {
    if (!columnCache.has(name)) {
      const res = await api.listColumns(tableIds.get(name));
      columnCache.set(name, res.body.data || []);
    }
    return columnCache.get(name);
  };

  let created = 0, skipped = 0;
  const problems = [];

  for (const table of manifest.tables) {
    const fks = table.columns.filter((c) => c.references);
    if (!fks.length) continue;
    const childId = tableIds.get(table.table);
    if (!childId) { problems.push(`${table.table}: table missing`); continue; }

    for (const col of fks) {
      const parentName = col.references.table;
      const parentId = tableIds.get(parentName);
      if (!parentId) { problems.push(`${table.table}.${col.name}: parent ${parentName} missing`); continue; }

      // A Catalyst foreign-key column is always bigint -- the type is fixed
      // by the platform, not by the parent it points at. A TEXT key such as
      // `curated_Section.ActCode -> curated_Act.ActCode` therefore cannot be
      // one: the column would reject its own values ("bigint value expected").
      // Left as a plain text column; the relationship stays application-level.
      if (col.type !== 'INTEGER') {
        problems.push(
          `${table.table}.${col.name} -> ${parentName}.${col.references.column} ` +
          `(${col.type} key; Catalyst foreign keys are bigint-only)`);
        continue;
      }

      const parentCols = await columnsOf(parentName);
      const target = parentCols.find((c) => c.column_name.toLowerCase() === col.references.column.toLowerCase());
      if (!target) {
        problems.push(`${table.table}.${col.name}: ${parentName}.${col.references.column} not found`);
        continue;
      }
      // Catalyst can only reference a uniquely-constrained column. Composite
      // primary keys leave their parts non-unique, so those relationships are
      // reported rather than forced -- the application enforces them instead.
      if (String(target.is_unique) !== 'true' && target.is_unique !== true) {
        problems.push(
          `${table.table}.${col.name} -> ${parentName}.${target.column_name} ` +
          `(parent column is not unique; likely part of a composite key)`);
        continue;
      }

      const childCols = await api.listColumns(childId);
      const existing = (childCols.body.data || []).find(
        (c) => c.column_name.toLowerCase() === col.name.toLowerCase());
      if (existing && String(existing.data_type).includes('foreign')
          && String(existing.parent_column) === String(target.column_id)) {
        skipped++;
        continue;
      }

      if (existing) {
        const del = await api.call('DELETE', `${api.tableUrl}/${childId}/column/${existing.column_id}`);
        if (!del.ok) {
          problems.push(`${table.table}.${col.name}: could not drop old column: ${del.text.slice(0, 120)}`);
          continue;
        }
        await sleep(200);
      }

      const res = await api.callWithRetry('POST', `${api.tableUrl}/${childId}/column`, [{
        column_name: col.name,
        data_type: 'foreign key',
        parent_table: parentId,
        parent_column: target.column_id,
        constraint_type: ON_DELETE,
        is_mandatory: Boolean(col.not_null || col.primary_key),
      }]);
      if (res.ok) {
        created++;
        log(`  + ${table.table}.${col.name} -> ${parentName}.${target.column_name}`);
      } else {
        problems.push(`${table.table}.${col.name}: ${res.text.slice(0, 160)}`);
      }
      await sleep(150);
    }
  }

  log(`\nForeign keys created: ${created}`);
  log(`Already correct     : ${skipped}`);
  if (problems.length) {
    log(`\n${problems.length} not created:`);
    for (const p of problems) log('  - ' + p);
  }
  log('\nColumns were recreated, so their values are gone. Reload with:');
  log('  node scripts/load_catalyst_data.js --truncate');
  return problems.length === 0;
}

/** Create a column, retrying throttled failures (see callWithRetry). */
function createColumnWithRetry(api, tableId, payload) {
  return api.callWithRetry('POST', `${api.tableUrl}/${tableId}/column`, payload);
}


// --------------------------------------------------------------- schema plan
/**
 * Map the SQLite-flavoured manifest onto Catalyst's type system.
 *
 * Two adaptations are forced by the platform, not chosen:
 *  - `Text` columns cannot carry a uniqueness constraint, so a TEXT primary
 *    key becomes `varchar` (max 255) to be unique-able at all.
 *  - Uniqueness is single-column only, so a composite primary key gets
 *    mandatory-but-not-unique columns; the combination is enforced by the
 *    application, not the store.
 */
function buildPlan(manifest) {
  const uniqueSingles = new Map();
  for (const idx of manifest.indexes) {
    if (idx.unique && idx.columns.length === 1) {
      if (!uniqueSingles.has(idx.table)) uniqueSingles.set(idx.table, new Set());
      uniqueSingles.get(idx.table).add(idx.columns[0]);
    }
  }

  return manifest.tables.map((t) => {
    const pks = t.columns.filter((c) => c.primary_key).map((c) => c.name);
    const compositePk = pks.length !== 1;
    const columns = t.columns.map((c) => {
      const singlePk = c.primary_key && !compositePk;
      const needsUnique = singlePk || (uniqueSingles.get(t.table)?.has(c.name) ?? false);
      let dataType;
      if (c.type === 'TEXT') dataType = needsUnique ? 'varchar' : 'text';
      else if (c.type === 'INTEGER') dataType = 'bigint';
      else if (c.type === 'REAL') dataType = 'double';
      else throw new Error(`Unmapped type ${c.type} on ${t.table}.${c.name}`);
      return {
        name: c.name,
        dataType,
        maxLength: dataType === 'varchar' ? 255 : dataType === 'text' ? 10000 : undefined,
        isMandatory: Boolean(c.not_null || c.primary_key),
        isUnique: needsUnique,
      };
    });
    return { table: t.table, compositePk: compositePk ? pks : null, columns };
  });
}

/**
 * Candidate column payloads, most-specific first.
 *
 * Probing beats guessing here: the endpoint distinguishes JSON_PARSE_ERROR
 * (wrong container) from INVALID_INPUT (right container, wrong fields), and an
 * array body produced the latter -- so the accepted shape is array-ish, but
 * the exact field set is not documented anywhere.
 */
function columnPayloadCandidates(tableId, tableName, col) {
  // The IaC project template (`catalyst iac:export`) renders these as JSON
  // numbers, while the GET response renders them as strings. The first live
  // probe sent strings and every array variant came back INVALID_INPUT, so
  // numeric is tried first here.
  const numeric = {
    table_id: tableId,
    table_name: tableName,
    column_name: col.name,
    data_type: col.dataType,
    is_mandatory: col.isMandatory,
    is_unique: col.isUnique,
    search_index_enabled: false,
    audit_consent: false,
    decimal_digits: 2,
  };
  if (col.maxLength !== undefined) numeric.max_length = col.maxLength;

  const numericNoTable = { ...numeric };
  delete numericNoTable.table_id;
  delete numericNoTable.table_name;

  const minimal = { column_name: col.name, data_type: col.dataType };
  if (col.maxLength !== undefined) minimal.max_length = col.maxLength;

  const stringy = {
    ...numeric,
    decimal_digits: '2',
    ...(col.maxLength !== undefined ? { max_length: String(col.maxLength) } : {}),
  };

  return [
    ['array numeric +table', [numeric]],
    ['array numeric -table', [numericNoTable]],
    ['array minimal', [minimal]],
    ['array stringy', [stringy]],
    ['object numeric', numeric],
    ['wrapped columns numeric', { columns: [numeric] }],
    ['wrapped column_details', { column_details: [numeric] }],
  ];
}

/** Find the accepted shape once, on a throwaway table, then reuse it. */
async function discoverColumnShape(api, log) {
  log('Discovering the accepted column payload shape on a scratch table...');
  const existing = await api.listTables();
  const stale = (existing.body.data || []).find((t) => t.table_name === PROBE_TABLE);
  if (stale) await api.deleteTable(stale.table_id);

  const made = await api.createTable(PROBE_TABLE);
  if (!made.ok) throw new Error(`Could not create the probe table: ${made.text.slice(0, 300)}`);
  const tid = made.body.data.table_id;

  try {
    const probeCol = {
      name: 'probe_col', dataType: 'text', maxLength: 10000,
      isMandatory: false, isUnique: false,
    };
    for (const [label, payload] of columnPayloadCandidates(tid, PROBE_TABLE, probeCol)) {
      const res = await api.createColumn(tid, payload);
      log(`  ${label.padEnd(24)} ${res.status} ${res.ok ? '<-- ACCEPTED' : res.text}`);
      if (res.ok) return label;
    }
    throw new Error(
      'No candidate column payload was accepted. The API contract has changed; ' +
      're-probe before trusting this script.'
    );
  } finally {
    await api.deleteTable(tid); // never leave the scratch table behind
  }
}

/** Compare the live schema against the manifest and report every gap. */
async function verifySchema(api, plan, log) {
  const list = await api.listTables();
  const byName = new Map((list.body.data || []).map((t) => [t.table_name, t.table_id]));

  let expected = 0, present = 0, missingTables = 0;
  const gaps = [];

  for (const t of plan) {
    expected += t.columns.length;
    const tid = byName.get(t.table);
    if (tid === undefined) {
      missingTables++;
      gaps.push(`${t.table}: TABLE MISSING (${t.columns.length} columns)`);
      continue;
    }
    const cols = await api.listColumns(tid);
    const have = new Set((cols.body.data || []).map((c) => c.column_name.toLowerCase()));
    const missing = t.columns.filter((c) => !have.has(c.name.toLowerCase()));
    present += t.columns.length - missing.length;
    if (missing.length) {
      gaps.push(`${t.table}: ${missing.length}/${t.columns.length} missing -> ${missing.map((m) => m.name).join(', ')}`);
    }
  }

  log('\n===== VERIFICATION =====');
  log(`Tables : ${plan.length - missingTables}/${plan.length} present`);
  log(`Columns: ${present}/${expected} present`);
  if (gaps.length) {
    log(`\nIncomplete (${gaps.length} tables):`);
    for (const g of gaps) log('  - ' + g);
    log('\nRe-run this script to fill the gaps; it only creates what is missing.');
  } else {
    log('\nSchema matches the manifest exactly.');
  }
  return gaps.length === 0;
}

function payloadFor(shapeLabel, tableId, tableName, col) {
  const found = columnPayloadCandidates(tableId, tableName, col).find(([l]) => l === shapeLabel);
  if (!found) throw new Error(`Unknown payload shape '${shapeLabel}'`);
  return found[1];
}

// -------------------------------------------------------------------- main
async function main() {
  const log = (...a) => console.log(...a);
  const project = readProject();
  const manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'));
  const plan = buildPlan(manifest);
  const totalCols = plan.reduce((n, t) => n + t.columns.length, 0);

  log(`Project : ${project.projectName} (${project.projectId})`);
  log(`Env     : ${project.envName} (${project.envId})`);
  log(`Manifest: ${plan.length} tables, ${totalCols} columns\n`);

  if (DRY_RUN) {
    for (const t of plan) {
      log(`${t.table}${t.compositePk ? `   [composite PK: ${t.compositePk.join(', ')} -- not enforced]` : ''}`);
      for (const c of t.columns) {
        log(`    ${c.name.padEnd(28)} ${c.dataType}${c.maxLength ? `(${c.maxLength})` : ''}` +
            `${c.isMandatory ? ' mandatory' : ''}${c.isUnique ? ' unique' : ''}`);
      }
    }
    log('\n--dry-run: no Catalyst CLI, credential or network access was used.');
    return;
  }

  // Deliberately after the dry-run return: previewing the plan needs only the
  // local manifest, so it must not require a CLI install or a login.
  const api = await connect(log);

  if (VERIFY_ONLY) {
    const ok = await verifySchema(api, plan, log);
    process.exitCode = ok ? 0 : 1;
    return;
  }

  if (FOREIGN_KEYS) {
    const ok = await provisionForeignKeys(api, manifest, log);
    process.exitCode = ok ? 0 : 1;
    return;
  }

  const shape = await discoverColumnShape(api, log);
  log(`Using column payload shape: ${shape}\n`);

  const before = await api.listTables();
  const existingTables = new Map((before.body.data || []).map((t) => [t.table_name, t.table_id]));

  let createdTables = 0, createdCols = 0, skipped = 0;
  const failures = [];

  for (const t of plan) {
    let tid = existingTables.get(t.table);
    if (tid === undefined) {
      const res = await api.createTable(t.table);
      if (!res.ok) { failures.push(`table ${t.table}: ${res.text.slice(0, 160)}`); continue; }
      tid = res.body.data.table_id;
      createdTables++;
      log(`+ table ${t.table}`);
    } else {
      log(`= table ${t.table} (exists)`);
    }

    const colRes = await api.listColumns(tid);
    const have = new Set((colRes.body.data || []).map((c) => c.column_name.toLowerCase()));

    for (const col of t.columns) {
      if (have.has(col.name.toLowerCase())) { skipped++; continue; }
      const res = await createColumnWithRetry(api, tid, payloadFor(shape, tid, t.table, col));
      if (res.ok) {
        createdCols++;
      } else {
        failures.push(`${t.table}.${col.name} (${col.dataType}): ${JSON.stringify(res.body).slice(0, 160)}`);
      }
      await sleep(150); // pace the loop; the API throttles under a tight burst
    }
  }

  log(`\nTables created : ${createdTables}`);
  log(`Columns created: ${createdCols}`);
  log(`Already present: ${skipped}`);
  if (failures.length) {
    log(`\n${failures.length} FAILURE(S) this run:`);
    for (const f of failures.slice(0, 15)) log('  - ' + f);
    if (failures.length > 15) log(`  ... and ${failures.length - 15} more`);
  }

  // Always re-read the live schema rather than trusting this run's own
  // counters -- the question that matters is what Catalyst actually has.
  const complete = await verifySchema(api, plan, log);
  process.exitCode = complete ? 0 : 1;
}

main().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
