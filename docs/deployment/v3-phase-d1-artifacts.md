# V3 Phase D1 — build and validate deployment artifacts

This phase closed the single largest gap `phase0-2-audit.md` flagged for
Phase 1: **Node.js/npm were not available in any prior session, so the React
console had never been built or served, even once.** This phase installed the
toolchain, built the console for the first time, and proved the console↔API
proxy actually works end-to-end locally — not just by code inspection.

## D1-1 — Rebuilt both deployment artifacts

```bash
python scripts/build_catalyst_artifact.py --target api
python scripts/build_catalyst_artifact.py --target refresh
```

| Target | Files | Bytes | Self-containment check |
|---|---|---|---|
| `cip-api` | 200 | 1,707,721 | `OK: ksp_cip imports with sys.path limited to the staging directory` |
| `cip-refresh` | 200 | 1,715,309 | same |

Both checked with `python -I` (isolated mode: no `PYTHONPATH`, no user
site-packages) and `sys.path` containing only the staging directory — a
repo-relative import "working" only because the script happened to run
inside the checkout would fail this, and does not.

## D1-2 — Built the React console (first time ever)

Toolchain installed this phase (was entirely absent before):

```text
node --version -> v24.18.0   (winget install -e --id OpenJS.NodeJS.LTS)
npm --version  -> 11.16.0    (bundled with the Node install)
```

```bash
cd frontend && npm ci && npm run build
```

```text
73 modules transformed, built in 1.35s
dist/index.html                  0.88 kB (gzip 0.47 kB)
dist/assets/index-CP_nW8BL.css   12.73 kB (gzip 3.27 kB)
dist/assets/index-D76Dcphm.js   194.52 kB (gzip 62.98 kB)
```

`npm ci` used the committed `package-lock.json` (not `npm install`), so this
is the locked dependency set, not whatever the registry currently resolves to.

`npm audit` reports 2 vulnerabilities (1 moderate, 1 high), both in
`esbuild`/`vite`'s **development server** (a CORS issue in the dev server
accepting requests from any origin — does not affect the static production
`dist/` output this deployment actually ships). Fixing it requires
`vite@8` — a breaking major-version jump — which was not applied, since an
untested breaking dependency bump is a worse risk than a dev-server-only
advisory that doesn't reach the deployed artifact. Recorded here rather than
silently left out of any report.

`dist/` was scanned for `api_key`/`secret`/`refresh_token`/`oauth` patterns:
none found.

## D1-3 — Proved the console↔API path locally, for the first time

Previously (`phase1-catalyst-runtime.md`): "Not exercised. Node.js and npm
are not installed in this environment." This phase ran the actual built
console against the actual API service, both locally:

```bash
# terminal 1: the API, as AppSail would run it
KSPCIP_DATASTORE_BACKEND=sqlite KSPCIP_ENVIRONMENT=local \
  X_ZOHO_CATALYST_LISTEN_PORT=8410 python catalyst/appsail/api/server.py

# terminal 2: the console, as AppSail would run it
X_ZOHO_CATALYST_LISTEN_PORT=8420 CIP_API_URL=http://127.0.0.1:8410 \
  node catalyst/appsail/console/server.js
```

| Check | Result |
|---|---|
| `GET /` (static `index.html`) | 200, correct `<title>Crime Intelligence Console — Karnataka State Police</title>` |
| `GET /assets/index-*.js` | 200, `content-type: text/javascript` |
| `GET /api/v1/health` through the console's proxy | 200, real health payload from the API service |
| `POST /api/v1/auth/login` through the console's proxy | 200, real JWT issued |

This is the first time in this project's history the console has been built
and the `/api` proxy exercised with a real request — everything before this
was verified at the API layer directly (`uvicorn` / `TestClient`), never
through the console's own Node process. The `http`/`https` protocol-selection
fix from the prior phase was exercised here too (target was `http://`, and it
worked — previously that branch of `server.js` had never actually run).

## D1-4 — Stack identifier correction

`catalyst functions:add --stack <stack>` and `catalyst appsail:add --stack <stack>`
(installed CLI, run with `--help`, no login required) both show the worked
example `python_3_9` — confirming the naming convention is
`python_<major>_<minor>`, underscore-separated. The prior guess
(`python3_11`/`python3.11`, taken from web documentation of a Flask example
that used a different display convention) was corrected in:

- `catalyst/catalyst.json` (both the `cip_refresh` function and `cip-api`
  AppSail entries)
- `catalyst/appsail/api/app-config.json`
- `catalyst/functions/cip_refresh/catalyst-config.json`

**Still open:** whether `3.11` specifically is an available version in a real
project — the CLI's help text confirms the *naming pattern*, not the
enumerated list of installable stack versions, which needs a logged-in
project (`catalyst functions:add` will list/validate against the real
options).

## Verify

- [x] API/refresh staging artifacts import in isolated mode.
- [x] Artifact manifests and hashes are generated
      (`dist/cip-api.manifest.json`, `dist/cip-refresh.manifest.json` — not
      committed; regenerate via the build script, which is the whole point of
      it being a script and not a checked-in binary).
- [x] React production bundle exists and contains no secrets.
- [x] Descriptor stack naming convention corrected and cited to CLI evidence;
      exact version availability still needs a live project.
- [x] Local defaults remain available only when explicitly selected — the
      console/API pair above ran with `KSPCIP_ENVIRONMENT=local` and
      `KSPCIP_DATASTORE_BACKEND=sqlite` set explicitly, exactly as a real
      Catalyst deployment would instead explicitly set the Catalyst
      selectors.

## Deliverable

This document, plus `docs/deployment/v3-catalyst-commands.md` (CLI evidence)
and the two artifact manifests (regenerable, not committed).
