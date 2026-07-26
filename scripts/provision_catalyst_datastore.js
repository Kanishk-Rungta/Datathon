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
const { execSync } = require('child_process');

const REPO_ROOT = path.resolve(__dirname, '..');
const MANIFEST = path.join(REPO_ROOT, 'docs/deployment/catalyst-schema-manifest.json');
const CATALYSTRC = path.join(REPO_ROOT, '.catalystrc');
const PROBE_TABLE = 'zz_provision_probe';

const DRY_RUN = process.argv.includes('--dry-run');

// ---------------------------------------------------------------- CLI module
// Resolved from the globally installed CLI rather than vendored, so this
// always matches the CLI the operator is actually logged in with.
function cliLibDir() {
  const override = process.env.ZCATALYST_CLI_LIB;
  if (override) return override;
  const root = execSync('npm root -g', { encoding: 'utf8' }).trim();
  const lib = path.join(root, 'zcatalyst-cli', 'lib');
  if (!fs.existsSync(lib)) {
    throw new Error(
      `Could not find the Catalyst CLI at ${lib}. Install it with ` +
      '`npm install -g zcatalyst-cli`, or set ZCATALYST_CLI_LIB.'
    );
  }
  return lib;
}

/** Authenticate exactly the way the CLI's own command_needs/auth.js does. */
async function getAccessToken(lib, dc) {
  const configStore = require(path.join(lib, 'util_modules/config-store.js')).default;
  const credential = require(path.join(lib, 'authentication/credential.js')).default;
  const stored = configStore.get(`${dc}.credential`);
  if (typeof stored !== 'string') {
    throw new Error(`No stored Catalyst credential for DC '${dc}'. Run \`catalyst login\`.`);
  }
  credential.initToken(stored, false);
  return credential.getAccessToken(); // never logged or persisted by this script
}

// ------------------------------------------------------------------- project
function readProject() {
  if (!fs.existsSync(CATALYSTRC)) {
    throw new Error(`${CATALYSTRC} not found. Run \`catalyst project:use\` in the repo root.`);
  }
  const rc = JSON.parse(fs.readFileSync(CATALYSTRC, 'utf8'));
  const project = rc.projects.find((p) => p.idx === rc.actives.project) || rc.projects[0];
  const env = project.env.find((e) => e.idx === rc.actives.env) || project.env[0];
  // The domain name carries the DC suffix, e.g. "...development" on .in.
  const dc = /\.zoho\.in|\.in$/.test(project.domain?.name || '') ? 'in' : 'us';
  return { projectId: project.id, projectName: project.name, envId: env.id, envName: env.name, dc };
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

// ------------------------------------------------------------------ HTTP
class Api {
  constructor({ base, token, projectId, envId, envName }) {
    this.tableUrl = `${base}/baas/v1/project/${projectId}/table`;
    this.headers = {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.catalyst.v2+json',
      'X-CATALYST-Environment': envName,
      'CATALYST-ORG': envId,
      'Content-Type': 'application/json',
    };
  }

  async call(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: this.headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await res.text();
    let parsed;
    try { parsed = JSON.parse(text); } catch { parsed = { raw: text }; }
    return { ok: res.status >= 200 && res.status < 300, status: res.status, body: parsed, text };
  }

  listTables() { return this.call('GET', this.tableUrl); }
  createTable(name) { return this.call('POST', this.tableUrl, { table_name: name }); }
  deleteTable(id) { return this.call('DELETE', `${this.tableUrl}/${id}`); }
  listColumns(id) { return this.call('GET', `${this.tableUrl}/${id}/column`); }
  createColumn(id, payload) { return this.call('POST', `${this.tableUrl}/${id}/column`, payload); }
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
  const core = {
    table_id: tableId,
    table_name: tableName,
    column_name: col.name,
    data_type: col.dataType,
    is_mandatory: col.isMandatory,
    is_unique: col.isUnique,
    search_index_enabled: false,
    audit_consent: false,
    decimal_digits: '2',
  };
  if (col.maxLength !== undefined) core.max_length = String(col.maxLength);

  const trimmed = {
    table_id: tableId,
    column_name: col.name,
    data_type: col.dataType,
    is_mandatory: col.isMandatory,
    is_unique: col.isUnique,
  };
  if (col.maxLength !== undefined) trimmed.max_length = String(col.maxLength);

  return [
    ['array-of-full', [core]],
    ['array-of-trimmed', [trimmed]],
    ['array-with-columns-key', [{ ...core, columns: undefined }]],
    ['object-full', core],
    ['wrapped-columns', { columns: [core] }],
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
      log(`  ${label}: ${res.status}${res.ok ? '  <-- accepted' : ` ${JSON.stringify(res.body).slice(0, 110)}`}`);
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

function payloadFor(shapeLabel, tableId, tableName, col) {
  const found = columnPayloadCandidates(tableId, tableName, col).find(([l]) => l === shapeLabel);
  if (!found) throw new Error(`Unknown payload shape '${shapeLabel}'`);
  return found[1];
}

// -------------------------------------------------------------------- main
async function main() {
  const log = (...a) => console.log(...a);
  const project = readProject();
  const lib = cliLibDir();
  process.env.CATALYST_ACTIVE_DC = process.env.CATALYST_ACTIVE_DC || project.dc;

  const manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'));
  const plan = buildPlan(manifest);
  const totalCols = plan.reduce((n, t) => n + t.columns.length, 0);

  log(`Project : ${project.projectName} (${project.projectId})`);
  log(`Env     : ${project.envName} (${project.envId})  DC=${project.dc}`);
  log(`Manifest: ${plan.length} tables, ${totalCols} columns\n`);

  if (DRY_RUN) {
    for (const t of plan) {
      log(`${t.table}${t.compositePk ? `   [composite PK: ${t.compositePk.join(', ')} -- not enforced]` : ''}`);
      for (const c of t.columns) {
        log(`    ${c.name.padEnd(28)} ${c.dataType}${c.maxLength ? `(${c.maxLength})` : ''}` +
            `${c.isMandatory ? ' mandatory' : ''}${c.isUnique ? ' unique' : ''}`);
      }
    }
    log('\n--dry-run: nothing was sent to Catalyst.');
    return;
  }

  const token = await getAccessToken(lib, project.dc);
  const base = require(path.join(lib, 'util_modules/constants')).ORIGIN.admin;
  const api = new Api({
    base, token,
    projectId: project.projectId, envId: project.envId, envName: project.envName,
  });

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
      const res = await api.createColumn(tid, payloadFor(shape, tid, t.table, col));
      if (res.ok) {
        createdCols++;
      } else {
        failures.push(`${t.table}.${col.name} (${col.dataType}): ${JSON.stringify(res.body).slice(0, 160)}`);
      }
    }
  }

  log(`\nTables created : ${createdTables}`);
  log(`Columns created: ${createdCols}`);
  log(`Already present: ${skipped}`);
  if (failures.length) {
    log(`\n${failures.length} FAILURE(S):`);
    for (const f of failures.slice(0, 40)) log('  - ' + f);
    if (failures.length > 40) log(`  ... and ${failures.length - 40} more`);
    process.exitCode = 1;
  } else {
    log('\nAll tables and columns are present.');
  }
}

main().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
