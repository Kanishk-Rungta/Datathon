# V3 Phase D3 — live Catalyst runtime

**KSP-CIP now runs on Catalyst.** This is the first phase in the project's
history where the application has actually executed on Zoho Catalyst rather
than being prepared to. Everything below was verified against the live
Development project, not by code inspection.

## Live services

| Component | Type | URL / name | State |
|---|---|---|---|
| `cip-api` | AppSail, `python_3_11` | `https://cip-api-50044361857.development.catalystappsail.in` | live, `/api/v1/health` 200 |
| `cip-console` | AppSail, `node18` | `https://cip-console-50044361857.development.catalystappsail.in` | live, serves the React bundle, `/api` proxy reaches `cip-api` |
| `cip_refresh` | Event Function, `python_3_11` | `cip_refresh` | deployed |

Project `KSP-CIP` (`54586000000013047`), environment `Development`
(`60080180501`), data centre **`.in`** — note the CLI and console are on
`catalyst.zoho.in`, not `.com`.

## The three defects that only a live deploy could have found

Each of these passed every local check and still broke in production. They are
recorded in full because "it worked locally" was exactly the failure mode.

### 1. AppSail does not install `requirements.txt`

The first deploy succeeded and the service returned:

```text
503  {"message":"Execution failed. Please check the startup command or port."}
```

The AppSail log showed the real cause:

```text
ModuleNotFoundError: No module named 'uvicorn'
  File "/catalyst/server.py", line 37, in <module>
```

**AppSail ships the source directory as-is and never runs `pip install`.**
`requirements.txt` in an AppSail artifact is inert documentation. Zoho's
documentation does not state this either way — it was established from the
crash. (Catalyst *Functions* behave differently: the CLI installs their
dependencies locally at deploy time, which is why `cip_refresh` needed a local
`python3.11` and `cip-api` did not.)

Fix: `scripts/build_catalyst_artifact.py` now vendors dependencies into
`vendor/` inside each Python artifact, and `catalyst/_bootstrap.py` puts that
directory on `sys.path` before any third-party import.

The vendoring is pinned to the **target** runtime, not the build machine:

```text
--platform manylinux2014_x86_64  --python-version 3.11  --only-binary=:all:
```

`--only-binary=:all:` is deliberate: with a cross-platform `--platform` pin pip
cannot build an sdist for the target, so permitting sdists would silently
produce a package compiled for Windows. Failing loudly on a package with no
manylinux wheel is the correct outcome. `--no-compile` is set for the same
class of reason — pip would otherwise byte-compile with the build
interpreter (3.13), writing `.pyc` files that are both useless on 3.11 and
~30 MiB of pure upload weight.

`verify_vendored()` checks this by **inspection, not import** — these are
Linux wheels and the build machine generally is not Linux, so importing them
locally would fail for reasons that say nothing about the artifact. It asserts
the required top-level modules exist and that no `.pyd`/non-Linux `.so` slipped
in, which is the failure mode where the `--platform` pin silently does not
apply and the artifact looks fine until it is deployed.

### 2. The console proxy forwarded the wrong `Host`

With the API healthy, `GET /api/v1/health` *through the console* returned a
bare Tomcat `HTTP 400 Bad Request` that never reached the application.

`server.js` passed `req.headers` straight through, so a request to the console
arrived at the API still carrying `Host: cip-console-…`. AppSail's front end
rejects that before routing. Locally both services were `127.0.0.1` on
different ports, where the mismatch is invisible — this could not have been
caught without two genuinely separate hosts.

Fix: `{ ...req.headers, host: target.host }`.

### 3. `ensure_directories()` created local paths regardless of backend

`Settings.ensure_directories()` unconditionally created the SQLite parent and
local filestore directories, even when both backends were Catalyst. Found
while investigating the 503; guarded per backend
(`backend/ksp_cip/config/settings.py`). Not the cause of that outage, but a
real latent fault in any sandbox with a read-only application directory.

## Deployment configuration

`catalyst.json` at the **repository root** is the CLI-managed descriptor and
points at the built artifacts, not the sources:

```json
{
  "appsail":   [{ "source": "dist\\cip-api", "name": "cip-api" },
                { "source": "dist\\cip-console", "name": "cip-console" }],
  "functions": { "source": "dist", "targets": ["cip-refresh"] }
}
```

`catalyst/catalyst.json` remains the hand-written design descriptor. Both
exist on purpose; the root one is generated/edited by `catalyst appsail:add`.

`dist/` is gitignored, so **a fresh checkout must build before it can deploy**:

```bash
python scripts/build_catalyst_artifact.py --target api
python scripts/build_catalyst_artifact.py --target console
python scripts/build_catalyst_artifact.py --target refresh
```

The console target is new in this phase: `server.js`'s original
`../../../frontend/dist` path only resolves inside a full checkout, so the
built bundle is now staged into the artifact as `dist/`, with `server.js`
choosing between the staged and checkout layouts the same way
`_bootstrap.py` does for `ksp_cip`.

## Interim environment selection — read this before believing the health output

`cip-api` currently runs with **local backends deliberately selected**:

```json
"env_variables": {
  "KSPCIP_ENVIRONMENT": "local",
  "KSPCIP_DATASTORE_BACKEND": "sqlite",
  "KSPCIP_SQLITE_PATH": "/tmp/ksp_cip.db",
  "KSPCIP_FILESTORE_ROOT": "/tmp/filestore"
}
```

This proves the **runtime** works. It is explicitly **not** the V3 target
state, and the deployment must not be described as "Catalyst-backed" while
this block is present. `_bootstrap.py` defaults `KSPCIP_DATASTORE_BACKEND` to
`catalyst`; this configuration overrides that default because the Data Store
schema does not exist yet (Phase D2) and no OAuth credentials are provisioned.
`/tmp` is used because it is writable and, being ephemeral, cannot be mistaken
for durable storage.

**Removing this block is the first step of the next phase**, once the schema
and credentials exist.

## Verify

- [x] `/api/v1/health` reachable through the deployed API — returns 200.
- [x] `/api/v1/health/ready` reflects configuration state —
      `configuration_valid: true`, `ready: false` with the honest hint that no
      data is seeded.
- [x] Console assets load from AppSail (correct `<title>`, hashed asset bundle).
- [x] Console `/api` calls reach the API without 502/400/protocol errors.
- [x] Refresh function deploys to the Event Function runtime.
- [ ] Circuit execution records run ID, stage, DQ and freshness — **not done**;
      deferred to after Phase D2, since the stages have no Data Store to act on.
- [ ] A failed stage is visible and retryable — **not done**, same reason.

## What is deliberately *not* claimed

- **The deployed instance has no data.** `seeded: false`, `cases: 0`. Seeding
  through `POST /admin/seed` requires `ADMIN_PIPELINE`, which requires a login,
  and user accounts are themselves created by the seeder — so a populated
  deployment genuinely depends on Phase D2, not on further runtime work. A
  login attempt against the live API returns a correct RFC 9457
  `authentication_failed` problem document, which does confirm routing, JSON
  handling and the error contract all work end-to-end.
- **Nothing here validates the Catalyst adapters.** Data Store, Stratus,
  NoSQL, Cache and Identity are all still unexercised against live services.
- **`python_3_11` is confirmed accepted** by both the AppSail and Function
  runtimes — this closes the open question from
  `v3-catalyst-commands.md`, which could previously only confirm the naming
  convention, not the version's availability.

## Toolchain added in this phase

| Tool | Why |
|---|---|
| Python 3.11.9 (`winget install -e --id Python.Python.3.11`) | The CLI vendors *function* dependencies locally and refuses to deploy without a matching interpreter: `unable to locate python3.11 in your system`. |
| `catalyst config:set python3_11.bin=<path>` | Points the CLI at that interpreter. Machine-local CLI config, not repository state. |
