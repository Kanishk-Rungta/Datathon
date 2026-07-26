/*
 * Shared Catalyst admin-API access for the provisioning and data-load scripts.
 *
 * Both scripts talk to endpoints Zoho does not document, using the CLI's own
 * login. That is worth having in exactly one place: the auth handshake, the
 * DC resolution and the retry policy are all things that must not drift
 * between the script that creates the schema and the script that fills it.
 *
 * Requires `catalyst login` and a `.catalystrc` in the repo root. The access
 * token is held in memory only -- never logged, printed or written to disk.
 */

'use strict';

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const CATALYSTRC = path.join(REPO_ROOT, '.catalystrc');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Locate the globally installed CLI, so this matches the login in use. */
function cliLibDir() {
  if (process.env.ZCATALYST_CLI_LIB) return process.env.ZCATALYST_CLI_LIB;

  const candidates = [];
  try {
    const root = execSync('npm root -g', { encoding: 'utf8' }).trim().split(/\r?\n/).pop();
    if (root) candidates.push(path.join(root, 'zcatalyst-cli', 'lib'));
  } catch { /* npm unreachable; fall through to known locations */ }

  const appData = process.env.APPDATA;
  if (appData) candidates.push(path.join(appData, 'npm', 'node_modules', 'zcatalyst-cli', 'lib'));
  const home = process.env.HOME || process.env.USERPROFILE;
  if (home) {
    candidates.push(path.join(home, 'AppData/Roaming/npm/node_modules/zcatalyst-cli/lib'));
    candidates.push(path.join(home, '.npm-global/lib/node_modules/zcatalyst-cli/lib'));
  }
  candidates.push('/usr/local/lib/node_modules/zcatalyst-cli/lib');
  candidates.push('/usr/lib/node_modules/zcatalyst-cli/lib');

  // Probe a file actually required later, not just the directory, so a
  // partial install fails here rather than at require() time.
  for (const dir of candidates) {
    if (fs.existsSync(path.join(dir, 'util_modules', 'config-store.js'))) return dir;
  }
  throw new Error(
    'Could not locate the Catalyst CLI. Tried:\n  ' + candidates.join('\n  ') +
    '\nInstall it with `npm install -g zcatalyst-cli`, or set ZCATALYST_CLI_LIB.'
  );
}

/**
 * The DC the CLI is logged in to.
 *
 * `.catalystrc` does not carry it, and guessing from the project domain
 * silently resolved a `.in` project to `us` -- wrong API host, and a
 * credential lookup under a DC the operator never logged in to.
 */
function resolveDC(lib) {
  if (process.env.CATALYST_ACTIVE_DC) return process.env.CATALYST_ACTIVE_DC;
  const configStore = require(path.join(lib, 'util_modules/config-store.js')).default;
  return configStore.get('active_dc', 'us');
}

/** Authenticate the way the CLI's own command_needs/auth.js does. */
async function getAccessToken(lib, dc) {
  const configStore = require(path.join(lib, 'util_modules/config-store.js')).default;
  const credential = require(path.join(lib, 'authentication/credential.js')).default;
  const stored = configStore.get(`${dc}.credential`);
  if (typeof stored !== 'string') {
    throw new Error(`No stored Catalyst credential for DC '${dc}'. Run \`catalyst login --dc ${dc}\`.`);
  }
  credential.initToken(stored, false);
  return credential.getAccessToken();
}

function readProject() {
  if (!fs.existsSync(CATALYSTRC)) {
    throw new Error(`${CATALYSTRC} not found. Run \`catalyst project:use\` in the repo root.`);
  }
  const rc = JSON.parse(fs.readFileSync(CATALYSTRC, 'utf8'));
  const project = rc.projects.find((p) => p.idx === rc.actives.project) || rc.projects[0];
  const env = project.env.find((e) => e.idx === rc.actives.env) || project.env[0];
  return { projectId: project.id, projectName: project.name, envId: env.id, envName: env.name };
}

class Api {
  constructor({ base, token, projectId, envId, envName }) {
    this.base = base;
    this.projectId = projectId;
    this.tableUrl = `${base}/baas/v1/project/${projectId}/table`;
    this.queryUrl = `${base}/baas/v1/project/${projectId}/query`;
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

  /**
   * Retry server-side failures.
   *
   * The first full provisioning run created 144 columns and then failed the
   * remaining 206 with INTERNAL_SERVER_ERROR -- not validation errors, and
   * uncorrelated with type or table. That is throttling under a tight loop,
   * so the answer is backoff, not a payload change.
   */
  async callWithRetry(method, url, body, attempts = 5) {
    let last;
    for (let i = 0; i < attempts; i++) {
      const res = await this.call(method, url, body);
      if (res.ok) return res;
      last = res;
      const code = res.body && res.body.data && res.body.data.error_code;
      const retriable = res.status >= 500 || res.status === 429 || code === 'INTERNAL_SERVER_ERROR';
      if (!retriable) return res;
      await sleep(500 * 2 ** i); // 0.5s, 1s, 2s, 4s, 8s
    }
    return last;
  }

  listTables() { return this.call('GET', this.tableUrl); }
  createTable(name) { return this.call('POST', this.tableUrl, { table_name: name }); }
  deleteTable(id) { return this.call('DELETE', `${this.tableUrl}/${id}`); }
  listColumns(id) { return this.call('GET', `${this.tableUrl}/${id}/column`); }
  createColumn(id, payload) { return this.call('POST', `${this.tableUrl}/${id}/column`, payload); }

  /** Insert a batch of rows. Body is a bare array, matching the column API. */
  insertRows(id, rows) {
    return this.callWithRetry('POST', `${this.tableUrl}/${id}/row`, rows);
  }

  zcql(query) {
    return this.callWithRetry('POST', this.queryUrl, { query });
  }
}

/** Everything a script needs to start talking to Catalyst. */
async function connect(log = () => {}) {
  const project = readProject();
  const lib = cliLibDir();
  const dc = resolveDC(lib);
  process.env.CATALYST_ACTIVE_DC = dc;
  const token = await getAccessToken(lib, dc);
  const base = require(path.join(lib, 'util_modules/constants')).ORIGIN.admin;

  log(`Project : ${project.projectName} (${project.projectId})`);
  log(`Env     : ${project.envName} (${project.envId})`);
  log(`DC      : ${dc}`);
  log(`API     : ${base}\n`);

  return new Api({ base, token, ...project });
}

module.exports = {
  REPO_ROOT, sleep, cliLibDir, resolveDC, getAccessToken, readProject, Api, connect,
};
