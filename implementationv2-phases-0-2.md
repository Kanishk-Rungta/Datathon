# KSP-CIP Implementation V2 — Phases 0, 1 and 2

## Purpose

This document is the execution plan for the first three implementation phases
of KSP-CIP V2. It is deliberately narrower than the full
[`implementationv2.md`](implementationv2.md): it describes the work required
to establish a safe baseline, make the application deployable on Zoho Catalyst,
and connect the Catalyst data plane without rewriting the application core.

The phases must be completed in order:

```text
Phase 0: reproducible baseline and release safety
        ↓
Phase 1: Catalyst runtime, packaging and deployment foundation
        ↓
Phase 2: Catalyst Data Store, Stratus and replay-safe data pipeline
```

Later work such as Catalyst Authentication, NoSQL/Cache, Bhashini ASR/NMT/TTS,
forecasting, financial ingestion, socio-economic indicators, and formal LLM
evaluation must build on these phases. They must not be used to hide an
incomplete deployment foundation.

## Scope and non-negotiable rules

1. **Catalyst is the deployment platform and system of record.** Use Catalyst
   Data Store, Stratus/File Store, AppSail or a supported Catalyst function
   runtime, and Catalyst-native job/event mechanisms. Do not add Firebase,
   Supabase, an external database, external object storage, or another hosting
   backend.
2. **The domain and five agents remain provider-independent.** Catalyst SDK
   calls belong in infrastructure adapters and the composition root, not in
   agents, analytics, graph code, or repository business rules.
3. **Synthetic data only in Phases 0–2.** The source-data connector is not
   silently enabled. A real FIR/CCTNS or financial connector needs a separate
   approved data contract and governance gate.
4. **Facts remain deterministic.** The LLM gateway may classify or polish text,
   but it cannot retrieve records, decide authorization, calculate values,
   create citations, or invent case facts.
5. **No person-level future-crime prediction.** Phase 2 can publish aggregate
   data freshness and historical intelligence. It must not turn an offender
   history score into a prediction about an individual.
6. **Every phase has an exit gate.** Do not begin the next phase until the
   previous phase has the listed evidence, automated tests, and a rollback
   procedure.

## Repository facts to use while implementing

These are the current seams and the current known risks. The code is the final
authority if a document differs from it.

| Area | Current implementation | Consequence for this plan |
|---|---|---|
| Application | FastAPI API, React console, five bounded agents, deterministic analytics, evidence composer | Preserve all application contracts; change wiring rather than agent logic |
| Local persistence | SQLite and local file store | Keep as the zero-credential development mode |
| Catalyst persistence | `CatalystDataStore` and a Stratus adapter exist, but live Catalyst operation is not proven | Complete and contract-test the adapters before production claims |
| LLM | `LocalDeterministicProvider` is an offline, LLM-compatible fallback, not neural model inference | Do not describe it as a running local model; keep it as the safe fallback |
| Catalyst API function | `catalyst/functions/cip_api/main.py` imports the ASGI app, but its current handler must be validated against the deployed Catalyst runtime | Do not assume the current `handler(context, basicio)` is deployable |
| Catalyst descriptors | `python3.9` is declared while the project requires Python `>=3.11` | Align runtime and dependency versions before deployment |
| Shared source | Function entrypoints calculate a path to `backend`, but deployment source folders do not automatically prove that the complete package is uploaded | Build and inspect a self-contained artifact |
| Migrations | SQLite `executescript`, `PRAGMA`, and schema SQL are used locally | Provision Catalyst DDL separately and add read-only compatibility checks |
| ZCQL adapter | Pagination and read-then-write behavior exist, but unsupported SQL patterns remain possible | Add capability tests for every repository query before loading data |
| Pipeline | `ingest`, `data_quality`, `intelligence`, and `retention` stage logic exists | Add durable run state, publication gating, idempotency, and Catalyst trigger verification |
| UI hosting | AppSail server serves `frontend/dist` and proxies `/api` through `CIP_API_URL` | Build the artifact inside deployment and fail clearly when API target is absent |

---

# Phase 0 — Reproducible baseline and release safety

## Phase 0 objective

Create a known-good local baseline and a controlled Catalyst Development
target. This phase changes no business behavior. Its purpose is to ensure that
every later deployment change can be compared with a working reference and
rolled back without losing synthetic data or test evidence.

## Phase 0 completion status

### Already present

- Local SQLite-backed application and synthetic data generator.
- Deterministic offline language/LLM fallback and no-credential startup path.
- Unit, integration, adapter, and pipeline test directories.
- Environment-driven settings and a `DataStore`/`FileStore` port boundary.
- Existing implementation and architecture documentation.

### Must still be completed

- A recorded baseline run from a clean checkout.
- A versioned dependency/runtime manifest.
- A dedicated Catalyst Development project/environment with synthetic data
  only.
- Deployment smoke tests that are opt-in and project-scoped.
- A secret/configuration inventory with no credentials in Git.
- A baseline report containing test results, seed counts, API checks, and known
  limitations.

## Phase 0 implementation steps

### P0-01 — Freeze the baseline

1. Create a working branch or immutable release tag for the current code.
2. Record the commit ID, operating system, Python version, Node/npm version,
   Catalyst CLI version (if installed), and dependency versions.
3. Do not include `.env`, OAuth refresh tokens, LLM keys, Bhashini keys,
   Catalyst project secrets, signed URLs, or generated case exports in the
   baseline commit.
4. Record the exact current limitations: local SQLite, local file store,
   local/demo identity, deterministic fallback provider, and synthetic data.

Suggested PowerShell commands from the repository root:

```powershell
git status --short
git rev-parse HEAD
python --version
node --version
npm --version
if (Get-Command catalyst -ErrorAction SilentlyContinue) { catalyst --version }
python -m pip freeze | Out-File .\docs\deployment\phase0-pip-freeze.txt
```

If a command is unavailable, record `not installed`; do not silently substitute
an unrecorded version.

### P0-02 — Install and validate the local toolchain

Use the repository setup script where a POSIX shell is available:

```bash
scripts/setup.sh
```

On Windows PowerShell, use the existing `.venv` or create one and install the
dependencies declared in `backend/pyproject.toml` plus the development extras:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".\backend[dev]"
```

The Python version must be 3.11 or newer. Do not solve a runtime mismatch by
loosening `requires-python`; Catalyst must be aligned to the application, not
the other way around.

### P0-03 — Run the local baseline

From a POSIX shell (Git Bash/WSL), run from the repository root:

```bash
./.venv/bin/python -m compileall backend
./.venv/bin/python -m pytest -q
./scripts/seed.sh 100 6 --reset
```

The first two commands also work in PowerShell with the Windows virtualenv
path. The `.sh` seed wrapper is for POSIX shells; the PowerShell-equivalent
seed command is shown below.

If the shell scripts are not executable on Windows, run their underlying
commands from `backend`:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ksp_cip.cli seed --cases 100 --months 6 --reset
```

Start the API in a second terminal and verify the health, OpenAPI, login, and
one evidence-bearing chat request:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m uvicorn ksp_cip.interface.api.main:get_app --factory --host 127.0.0.1 --port 8000
```

Then check `http://127.0.0.1:8000/api/v1/docs` and exercise a deterministic
question such as a FIR lookup, a trend, and an empty result. The response must
include the expected evidence locators and computation trace.

### P0-04 — Create a test and evidence matrix

Create `docs/deployment/phase0-baseline.md` containing a row for each of the
following scenarios:

| Scenario | Expected proof |
|---|---|
| Investigator FIR query | Only the investigator's permitted unit scope is retrieved |
| Statewide analyst query | Statewide aggregate is allowed, named data policy is respected |
| Policymaker query | Aggregate-only response; no named person/case detail |
| Empty result | The answer contains a query/aggregate evidence locator |
| Inferred graph link | Edge is marked `(inferred)` and has source/derivation evidence |
| Synthetic financial result | Output is marked `(synthetic extension)` |
| LLM disabled | Factual response still works through deterministic path |
| PDF export | Export is watermarked and tied to the requesting user |
| DQ blocking failure | New intelligence is not published |
| Repeat seed | No duplicate rows or duplicate derived edges |

Save response status, correlation ID, test data size, and the commit ID. Do
not save real or sensitive data in the report.

### P0-05 — Establish Catalyst Development configuration

Create a separately named Catalyst Development project/environment. Use a
service identity with the minimum permissions needed for development. Record
the project identifier and environment name in a local ignored file or secret
manager, not in source.

At minimum, identify values for:

```text
KSPCIP_ENVIRONMENT
KSPCIP_DATASTORE_BACKEND
KSPCIP_FILESTORE_BACKEND
KSPCIP_KEYVALUE_BACKEND
KSPCIP_CACHE_BACKEND
KSPCIP_IDENTITY_BACKEND
KSPCIP_CATALYST_PROJECT_ID
KSPCIP_CATALYST_ENVIRONMENT
KSPCIP_CATALYST_BASE_URL
KSPCIP_CATALYST_OAUTH_CLIENT_ID
KSPCIP_CATALYST_OAUTH_CLIENT_SECRET
KSPCIP_CATALYST_OAUTH_REFRESH_TOKEN
KSPCIP_CATALYST_STRATUS_BUCKET
```

For Phase 0, leave the local backends enabled until Phase 2 passes its live
checks. A configuration file may document future values, but it must not
pretend that a service is live.

### P0-06 — Add opt-in deployment smoke tests

Add a `deployment` pytest marker and a fixture that refuses to run unless all
of the following are explicitly set:

- a Catalyst project ID;
- an environment name equal to the intended Development environment;
- an explicit opt-in variable such as `KSPCIP_RUN_CATALYST_TESTS=1`; and
- a non-production safety flag.

The fixture must fail closed when a variable is absent. It must never infer the
target from a default project or run against Production.

### P0-07 — Document the rollback point

The rollback procedure for this phase is:

1. stop any deployment job;
2. restore the baseline tag/commit;
3. unset all Catalyst backend selectors;
4. remove only the temporary Development artifacts if necessary; and
5. rerun the local baseline test and seed commands.

No user-facing or production data is deleted in Phase 0.

## Phase 0 verification and exit gate

Phase 0 is complete only when all items below are attached to the baseline
report:

- [ ] Clean local setup succeeds on Python 3.11+.
- [ ] `compileall` passes.
- [ ] The full local test suite passes, or each failure has an issue with an
      owner and explicit reason.
- [ ] A small seed creates the expected curated and intelligence records.
- [ ] API health, OpenAPI, authentication, FIR retrieval, analytics, graph,
      empty-result, and PDF paths have been exercised.
- [ ] The LLM provider can be disabled without changing factual payloads.
- [ ] No secret, token, sensitive export, or real case data is tracked.
- [ ] Catalyst Development target and opt-in smoke-test guard are documented.
- [ ] Rollback to the baseline commit is tested.

**Exit artifact:** `docs/deployment/phase0-baseline.md` plus the test output and
the exact commit/runtime manifest.

---

# Phase 1 — Catalyst runtime, packaging and deployment foundation

## Phase 1 objective

Deploy the application shell to Catalyst Development with the same API and UI
contracts as the local application. Phase 1 does not yet declare live Catalyst
data persistence complete; it makes the runtime, packaging, entrypoints,
configuration, health checks, and deployment boundaries reliable enough for
Phase 2.

## Phase 1 completion status

### Already present

- Catalyst descriptors for an API function, refresh function, AppSail console,
  Stratus, cache, and a nightly circuit.
- A Catalyst-specific function source directory for the API and refresh stage.
- A Node AppSail proxy that expects `frontend/dist` and `CIP_API_URL`.
- Container-level provider selection for Data Store, file store, KV, cache,
  identity, language, and LLM implementations.

### Must still be completed before claiming deployability

- Resolve the Python version mismatch (`python3.9` descriptors versus the
  project's Python `>=3.11` requirement).
- Verify and, if required, replace the API entrypoint with the exact Catalyst
  runtime contract. The current handler signature must not be assumed correct.
- Package the complete `backend/ksp_cip` tree and required prompt/lexicon/schema
  files into every function artifact.
- Add all runtime dependencies used by deployed code, including `httpx` if a
  hosted provider or HTTP adapter is enabled.
- Make `KSPCIP_ENVIRONMENT` a value accepted by the settings enum (or map the
  Catalyst deployment name to an accepted application environment).
- Ensure Catalyst deployments cannot silently use local file storage or local
  auth defaults.
- Build and verify `frontend/dist` inside the AppSail artifact and configure
  `CIP_API_URL` and production CORS.
- Add health/readiness, correlation ID, provider-status, and startup fail-fast
  checks.

## Phase 1 implementation steps

### P1-01 — Select and document the supported Catalyst hosting shape

Before editing code, confirm which Catalyst runtime is available in the target
region for the FastAPI application:

1. If the supported Advanced I/O runtime provides the required HTTP/ASGI bridge,
   implement the exact documented request handler and test it with a deployed
   request.
2. If it does not provide a reliable ASGI bridge, run the FastAPI application
   as the supported Catalyst AppSail Python web application and use a thin
   Catalyst-compatible entrypoint. Do not leave an unverified function shim in
   front of FastAPI.
3. Keep `ksp_cip.interface.api.main:get_app` as the only application factory.
   The Catalyst adapter must translate the runtime request into that app; it
   must not duplicate routes or business logic.

Record the decision and the exact runtime contract in
`docs/deployment/catalyst-runtime.md`.

### P1-02 — Align runtime versions and descriptors

1. Set the API and refresh descriptors to a Python stack supported by both
   Catalyst and `backend/pyproject.toml` (Python 3.11+ is required by this
   repository).
2. Use one source of truth for package versions. Generate a lock/export for
   the deployment artifact and compare it with local versions.
3. Fail the build if the declared runtime is below the project requirement.
4. Keep Node 18+ for the AppSail console, or use the supported Node version
   selected by the deployed Catalyst runtime and record it.

Do not change application code to accommodate Python 3.9 unless the project
requirement and all dependencies have first been formally revalidated.

### P1-03 — Build a self-contained function artifact

The current entrypoint computes a path to a repository-level `backend` folder.
That is valid locally but is not proof that Catalyst uploads that folder. Build
the artifact so it contains:

```text
function artifact/
  main.py
  requirements.txt or locked dependency export
  ksp_cip/
    application/
    config/
    domain/
    infrastructure/
    interface/
  infrastructure/db/schema.sql
  prompts/ and lexicon/ files required at runtime
```

Use a build script rather than manually copying files. The script must:

1. create a clean staging directory;
2. copy only the required source and resource files;
3. install or vendor the declared dependencies into the deployment format;
4. compile/import the package in the staging directory;
5. emit a file manifest with hashes; and
6. fail if imports resolve from the developer's working tree.

The same packaging function should be used for the API and refresh artifacts,
with only the entrypoint differing. This prevents one function from receiving
an older copy of an agent or schema.

### P1-04 — Correct configuration selection and fail-fast behavior

Update settings and deployment environment mapping so that:

- the deployment name `catalyst` is translated to a valid application
  environment, or the enum explicitly includes it;
- Catalyst selectors are all explicit (`datastore`, `filestore`, `keyvalue`,
  `cache`, and identity), rather than relying on local defaults;
- non-local environments reject the development JWT secret;
- Catalyst Data Store selection requires project/OAuth configuration;
- Catalyst file storage is mandatory whenever Catalyst Data Store is selected;
- local directory creation is not required for a read-only Catalyst function
  filesystem; and
- hosted LLM/Bhashini providers fail at startup when their required keys are
  absent, while the deterministic local fallback remains available only when
  explicitly selected.

Do this in `config/settings.py` and the container binding. Do not add provider
conditionals to agents or repositories.

### P1-05 — Add deployment health and readiness checks

Expose a health response that distinguishes:

- process alive;
- configuration valid;
- application package loaded;
- selected provider names;
- Catalyst service reachability (when enabled);
- schema compatibility (Phase 2); and
- degraded optional services such as Bhashini or hosted LLM.

Do not include secrets, raw prompts, FIR text, OAuth responses, or row contents
in health output. Include a correlation/request ID in API responses and logs.

The process should fail readiness, not necessarily liveness, when an optional
provider is degraded. It must fail readiness when the selected system-of-record
backend is unavailable or misconfigured.

### P1-06 — Make the refresh entrypoint runtime-safe

Keep the existing `run_stage(stage)` application service. Replace only the
Catalyst event wrapper required by the actual event/function contract:

1. parse a bounded event payload;
2. validate the stage against `ingest`, `data_quality`, `intelligence`, and
   `retention`;
3. create a container using deployment settings;
4. write a run-start record and correlation ID;
5. call the stage;
6. write success/failure and a bounded error category; and
7. return the runtime's documented success/failure response.

Do not put pipeline logic into the event wrapper. It remains a thin adapter.

### P1-07 — Build and serve the React console correctly

1. Run `npm ci` or the approved lockfile-based install in the frontend build.
2. Run the production build and verify that `frontend/dist/index.html` and all
   referenced assets exist.
3. Copy the build output into the AppSail staging artifact, or build it in the
   AppSail build step; do not depend on a developer's local `frontend/dist`.
4. Set `CIP_API_URL` to the deployed API origin through Catalyst configuration.
5. Configure allowed origins in FastAPI for the deployed console domain.
6. Verify browser requests use the proxy and do not expose OAuth secrets or
   provider keys in JavaScript.

### P1-08 — Add dependency, secret, and artifact checks

The deployment build must fail for:

- missing imports;
- Python version below the repository requirement;
- missing `httpx` when a hosted LLM/Bhashini HTTP adapter is selected;
- checked-in secret-like values;
- a missing `frontend/dist` artifact;
- unresolved local source imports; or
- local persistence selected in a Catalyst deployment.

Secrets are supplied through Catalyst configuration/secrets. They are never
placed in `catalyst.json`, source files, frontend bundles, test fixtures, or
PDFs.

### P1-09 — Preserve the identity boundary

Phase 1 only establishes the boundary. Keep local demo identity for local tests,
but make the API dependency capable of selecting a Catalyst identity provider
in a later phase. Verify that every router still receives the existing
`Principal`, role, permission, and unit scope object. No agent may inspect a
Catalyst token directly.

## Phase 1 execution sequence

Run the following in order after Phase 0 is green:

1. Implement the build/staging script and descriptor/runtime corrections.
2. Build API and refresh artifacts locally and inspect their manifests.
3. Run import, compile, unit, and API smoke tests from the staging artifacts,
   not only from the repository checkout.
4. Build the frontend and run the AppSail server with a local API target.
5. Deploy only to the Catalyst Development project using the Catalyst CLI or
   Catalyst console deployment workflow recorded in
   `docs/deployment/catalyst-runtime.md`.
6. Exercise the deployed API health and OpenAPI endpoints.
7. Exercise the deployed UI login and one read-only synthetic query. Until
   Phase 2 passes, the data backend may remain the explicitly selected local
   Development backend only if the runtime supports it; it must not be
   presented as a production deployment.
8. Capture deployment ID, artifact hash, runtime version, logs, response status,
   and rollback instructions.

## Phase 1 verification matrix

| Check | How to execute | Pass condition |
|---|---|---|
| Clean artifact import | Run Python import/compile from staging directory | No import resolves from checkout; no missing resource |
| Runtime compatibility | Inspect descriptor and deployment runtime | Python runtime satisfies `>=3.11` and dependency set installs |
| API entrypoint | Call deployed health and OpenAPI URLs | Runtime returns valid HTTP responses through the documented contract |
| API behavior | Run the Phase 0 query corpus against deployed API | Same intent, payload, evidence locators, scope and safety labels as local |
| UI artifact | Open AppSail URL and inspect network requests | Static assets load; `/api` reaches API; no 502 from missing `CIP_API_URL` |
| Configuration fail-fast | Deploy with one required value removed in a disposable environment | Readiness fails with a safe configuration error; process does not silently use local state |
| Secret hygiene | Scan artifact, logs, and browser bundle | No API key, refresh token, JWT secret, or signed URL appears |
| Rollback | Redeploy Phase 0/previous artifact | Health and local baseline behavior return |

## Phase 1 exit gate

- [ ] Runtime and descriptors are aligned with the Python/Node requirements.
- [ ] API and refresh artifacts are self-contained and hash-manifested.
- [ ] The API entrypoint has been tested against the actual Catalyst contract.
- [ ] The deployed UI loads a production build and reaches the API through the
      configured proxy.
- [ ] Catalyst configuration cannot silently select local file storage,
      development JWTs, or missing provider credentials.
- [ ] Health/readiness and correlation IDs are visible without sensitive data.
- [ ] The Phase 0 query/evidence corpus passes against the deployed shell.
- [ ] Deployment and rollback runbooks exist and have been exercised.

**Exit artifact:** `docs/deployment/phase1-catalyst-runtime.md`, artifact
manifests, deployment logs, smoke-test output, and the previous artifact ID.

---

# Phase 2 — Catalyst Data Store, Stratus and replay-safe pipeline

## Phase 2 objective

Move the authoritative relational and file responsibilities to Catalyst while
preserving the current repositories, agents, analytics, evidence model, and
pipeline stage interfaces. Phase 2 is complete only when a small synthetic
dataset can be loaded, quality-gated, refreshed, queried, exported, replayed,
and rolled back in Catalyst Development.

## Phase 2 completion status

### Already present

- `DataStore` port and `CatalystDataStore` adapter seam.
- Local schema in `backend/ksp_cip/infrastructure/db/schema.sql`.
- Batch/control, loader, DQ, intelligence refresh, and retention stages.
- A Stratus/FileStore adapter boundary.
- Evidence, authorization, audit, PDF export, and derived-intelligence tables.
- Contract tests for selected Catalyst adapter behavior.

### Must still be completed

- Live Catalyst Data Store table provisioning from a Catalyst-compatible schema
  process; SQLite migration scripts cannot be executed unchanged.
- A schema/version manifest and live table/column compatibility check.
- Removal or translation of SQLite-only SQL (`executescript`, `PRAGMA`,
  `INSERT OR REPLACE`, `ON CONFLICT`, and duplicate adapter pagination).
- A complete Stratus adapter test against the Development bucket, including
  ownership and traversal protection.
- Durable run control and publication markers so failed DQ cannot expose new
  intelligence.
- Idempotent/retry-safe writes and replay tests for every pipeline stage.
- SQLite-versus-Catalyst record/count/checksum comparison on the same seed.
- Backup/restore and last-known-good rollback evidence.

## Phase 2 data layout

Use one Catalyst project and an explicitly named Development dataset. Preserve
the existing source/enrichment separation:

```text
Catalyst Data Store
  source/curated FIR mirror tables (organiser schema)
  cip_* control, evidence, identity, graph, analytics and audit tables
  ext_* explicitly synthetic/approved extension tables

Catalyst Stratus bucket: cip-ingest
  landing/{batch_id}/{source_table}.ndjson
  raw/{batch_id}/...
  manifests/{batch_id}.json
  exports/{user_id}/{session_id}/{file}.pdf
  audio/{user_id}/{session_id}/{content-hash}.*   # later phase, if enabled
```

Do not widen organiser source columns to make a feature easier. Add a `cip_` or
approved `ext_` table with provenance instead.

## Phase 2 implementation steps

### P2-01 — Provision the Catalyst schema outside application startup

1. Treat `schema.sql` as the semantic source of required tables and fields, not
   as a script to send directly to ZCQL.
2. Generate a Catalyst provisioning manifest containing table name, column name,
   type, required/nullable status, key, and index requirements.
3. Create the Data Store tables using the supported Catalyst console/CLI/IaC
   workflow for the target project. Record the exact command or exported
   provisioning definition in `docs/deployment/catalyst-schema.md`.
4. Create a control table for schema version and deployment compatibility.
5. Apply forward-only schema changes through reviewed provisioning steps. Do
   not call SQLite `executescript()` from a Catalyst request or function.
6. Run a read-only startup/deployment check that verifies required tables and
   required columns. Refuse readiness if a required item is missing or has an
   incompatible type.

The application may retain SQLite migrations for local development. The
Catalyst adapter must use a separate migration/provisioning path behind the
same schema-version contract.

### P2-02 — Add a portable schema capability interface

The current loader uses SQLite-specific introspection such as `PRAGMA
table_info`. Add a narrow `DataStore.table_columns(table_name)` capability (or
an equivalent schema inspector) to the port and implement it separately for
SQLite and Catalyst.

Then change the loader/schema validation service to call the port. It must not
construct `PRAGMA` in a repository query. The local implementation can use
SQLite metadata; Catalyst can read from its schema manifest or supported table
metadata endpoint.

This is a small boundary change: loader, DQ, and migration checks change their
metadata call; business repositories and agents keep their existing method
signatures.

### P2-03 — Make all repository SQL ZCQL-compatible

Inventory every query with a static scan and the adapter contract tests. At
minimum address:

1. `INSERT OR REPLACE`: replace with an adapter-neutral read-then-write upsert
   using a deterministic natural key, or a Catalyst-supported update/insert
   sequence.
2. SQLite `ON CONFLICT` clauses: translate to the same explicit upsert port;
   do not emit unsupported syntax to ZCQL.
3. `PRAGMA`: move to the schema capability interface.
4. `executescript`: use only in SQLite migrations; never in Catalyst.
5. Pagination: the repository must express one logical page and the Catalyst
   adapter must own provider page iteration. Do not append a second `LIMIT` to
   SQL that already contains one. Add a test for `LIMIT`, offset, ordering, and
   a result set larger than one provider page.
6. Parameter binding: retain parameterized queries and test quotes, Unicode,
   nulls, long values, and malicious SQL-shaped strings.

Preserve repository method names and returned dictionaries so application
services, agents, and response schemas do not need rewriting.

### P2-04 — Harden `CatalystDataStore`

Extend the adapter rather than replacing the repositories:

- normalize scalar parameters and ZCQL escaping in one place;
- enforce table/column allow-lists where the adapter builds identifiers;
- paginate until the provider reports no more rows, with a hard safety cap;
- retry only idempotent reads and idempotent batch writes with bounded backoff;
- include provider request ID, operation type, duration, page count, and error
  category in logs, never row values;
- convert provider errors into the application's typed storage errors; and
- expose a health/schema check used by Phase 1 readiness.

Add contract tests for every repository SQL shape, not only hand-written adapter
examples.

### P2-05 — Implement and verify the Catalyst Stratus FileStore

Complete the existing `FileStore` implementation with the same interface as the
local file store: `write_bytes`, `write_text`, `read_bytes`, `exists`,
`list_keys`, and `url_for`.

Required behavior:

1. Normalize object keys and reject `..`, absolute paths, backslashes where
   unsafe, and control characters before calling Catalyst.
2. Store landing files, manifests, exports, and future audio under the prefixes
   in the data layout above.
3. Record content type, byte length, checksum, object key, owner, and
   correlation ID for uploads/exports.
4. Return a short-lived signed URL or an application-authorized download route;
   never make case exports public.
5. Preserve existing `authorize_file_access` behavior. A Catalyst URL is issued
   only after that policy succeeds.
6. Test missing objects, partial uploads, retries, checksum mismatch, and
   unauthorized owner access.

### P2-06 — Add durable batch/run control

Every pipeline run needs a durable identity and state. Add or use control tables
with at least:

```text
run_id, batch_id, stage, status, input_manifest_hash,
started_at, completed_at, row_counts, dq_summary,
published_version, error_category, correlation_id
```

The state machine should be forward-only and replay-safe:

```text
registered → landed → loaded → dq_passed → refreshed → published
                         ↘ dq_failed / failed
```

Rules:

- A retry of the same `batch_id` and manifest hash is a no-op or a safe resume.
- A changed manifest hash creates a new run; it must not overwrite the old
  successful run silently.
- The active intelligence version points to the last successfully published
  run, not merely the most recently written rows.
- DQ failure records findings and leaves the previous published version active.
- Every stage can be restarted independently from its recorded predecessor.

### P2-07 — Separate load from publication

The current design must not allow a failed DQ run to leave query-visible new
intelligence. Implement one of these Catalyst-compatible patterns:

1. write curated and derived rows with a `run_id`/`publication_version`, then
   atomically switch a small active-version control record after DQ and refresh
   succeed; or
2. write to staging tables/partitions, validate, then promote by updating the
   active run pointer.

The API repositories must include the active publication predicate when reading
derived intelligence. Keep the previous version until the new one is complete.

This is a deterministic publication rule; it does not require an agent or LLM.

### P2-08 — Make the four refresh stages Catalyst-safe

Keep the existing stage names and application services:

1. **`ingest`** — enumerate registered Stratus manifests, validate checksums,
   load only pending/replay-safe batches, and record row counts.
2. **`data_quality`** — execute schema, referential, duplicate, null, date,
   scope, and source-integrity checks. Mark a run failed when a blocking rule
   fails.
3. **`intelligence`** — refresh entity links, graph edges, centrality,
   embeddings, hotspots, alerts, and recorded-history/case-priority outputs for
   the run's curated version. Do not publish until completion.
4. **`retention`** — purge only data allowed by the retention policy and record
   what was removed. It must never remove the active publication or audit data.

The Catalyst event wrapper should pass only a stage and run/batch reference. It
must not fetch an unbounded dataset into memory or make stage order implicit.

### P2-09 — Run a deterministic SQLite-to-Catalyst comparison

Use the same small synthetic seed and manifest in both backends:

1. reset and seed SQLite;
2. export the exact landing NDJSON/manifests;
3. load the same files into Catalyst Development;
4. run DQ and intelligence refresh in both environments;
5. compare source row counts, primary-key sets, aggregate totals, graph edge
   keys, alert IDs, embedding IDs, and publication version; and
6. compare evidence locator sets for the fixed Phase 0 query corpus.

Values that are intentionally provider-specific (timestamps, request IDs,
signed URLs) must be excluded from equality comparison and listed explicitly.

### P2-10 — Test failure, retry and rollback paths

Inject each failure in a disposable Development run:

- malformed NDJSON;
- checksum mismatch;
- duplicate primary key;
- missing foreign key;
- unsupported SQL/query shape;
- provider timeout during a read;
- provider timeout after an idempotent write;
- DQ blocking failure;
- intelligence refresh failure; and
- Stratus export read failure.

For every failure, verify that:

- the run status and error category are durable;
- the last-good publication remains queryable;
- a retry is safe and does not duplicate rows;
- no partial export is presented as complete; and
- audit/correlation records exist without sensitive payload leakage.

### P2-11 — Verify export ownership and recovery

1. Export a conversation PDF from Catalyst Stratus.
2. Download it through the authorized application route.
3. Attempt access as another user and as an unauthorized role; both must fail.
4. Verify watermark, evidence notice, owner prefix, content type, checksum, and
   expiry behavior.
5. Export or back up the synthetic curated/control tables and landing manifests.
6. Restore them into an isolated Development project and rebuild derived
   intelligence from curated data. Derived graph/embedding/alert tables must
   not be the only copy of the inputs.

## Phase 2 execution sequence

Execute only after the Phase 1 shell is green:

1. Generate the schema/provisioning manifest and create Catalyst Development
   tables/bucket.
2. Implement the schema capability port and remove SQLite metadata calls from
   shared application paths.
3. Run the SQL compatibility inventory and fix each unsupported statement at
   the adapter/repository boundary.
4. Complete Stratus/FileStore and ownership tests.
5. Add run control, active publication, DQ blocking, and safe retry behavior.
6. Deploy the API and refresh artifact that contains these changes.
7. Register a small synthetic landing batch in Stratus and run `ingest`.
8. Run `data_quality`; intentionally inject one blocking error and verify that
   the previous active version remains visible.
9. Run `intelligence`, promote only after success, and verify counts/queries.
10. Run the replay, timeout, export, unauthorized-download, and restore tests.
11. Compare Catalyst output with the SQLite baseline and publish the phase
    report.

## Phase 2 verification matrix

| Check | How to execute | Pass condition |
|---|---|---|
| Schema compatibility | Run deployment/readiness schema check | Required tables/columns/types match the manifest |
| Query compatibility | Run all repository contract tests against Catalyst | No SQLite-only syntax reaches ZCQL |
| Pagination | Seed more than one provider page and query ordered results | No duplicates, gaps, or second `LIMIT` error |
| Parameter safety | Query names containing quotes, Unicode and SQL-like text | Correct result or safe empty result; no query injection |
| Landing integrity | Upload manifest and NDJSON with checksum | Loader rejects missing/mismatched objects |
| Idempotent ingest | Submit identical batch twice | Same row/derived counts; no duplicates |
| DQ publication gate | Inject a blocking error after writes | New version is not active; last-good version remains visible |
| Intelligence refresh | Run all refresh steps on approved batch | Graph, embeddings, hotspots, alerts and scores are linked to the run |
| Provider retry | Force read timeout and retry | Bounded retry; no duplicate mutation; durable error if exhausted |
| Scope enforcement | Query as investigator, analyst and policymaker | Retrieval/graph scope is enforced before response composition |
| Export security | Download as owner and non-owner | Owner succeeds; non-owner is denied; object is not public |
| Recovery | Restore synthetic backup to isolated project | Curated data restores and derived intelligence rebuilds |
| Local parity | Compare same manifest against SQLite | Required IDs, counts, evidence and payloads match |

## Phase 2 exit gate

- [ ] Catalyst Data Store schema is provisioned and version-checked.
- [ ] Catalyst Stratus landing/export paths are live and access-controlled.
- [ ] All shared metadata operations are portable; no `PRAGMA` or
      `executescript` reaches Catalyst.
- [ ] Unsupported upsert/pagination patterns are fixed and contract-tested.
- [ ] Batch/run state, DQ status, publication version, and correlation IDs are
      durable.
- [ ] DQ failure cannot publish new intelligence.
- [ ] Replaying the same manifest is safe and produces no duplicates.
- [ ] SQLite and Catalyst agree on the approved parity corpus.
- [ ] API agents still receive the same domain repositories/ports and no
      Catalyst SDK code has leaked into agent logic.
- [ ] Export ownership, signed URL/application-route protection, backup, and
      restore have been exercised.
- [ ] A rollback to the previous active publication and previous deployment
      artifact has been tested.

**Exit artifact:** `docs/deployment/phase2-data-plane.md`, schema manifest,
artifact hashes, Catalyst Development run IDs, parity report, failure-injection
report, export authorization report, and restore evidence.

---

# Minimal-change compatibility map

The implementation must preserve these existing contracts:

| Existing contract | Phase 0–2 change | Must not change |
|---|---|---|
| `DataStore` | Add schema inspection/portable upsert capability where needed | Repository service method signatures and returned shapes |
| `FileStore` | Add Catalyst Stratus implementation and secure URL behavior | PDF/export service and owner authorization policy |
| `build_container()` | Bind providers from settings and validate configuration | Agent construction and domain dependencies |
| Pipeline stage names | Add run IDs, publication state, retries and runtime wrapper | `ingest`, `data_quality`, `intelligence`, `retention` semantics |
| `Principal` and `PrincipalDep` | Prepare Catalyst identity binding and fail-closed config | Router/agent authorization interfaces |
| Evidence model | Add Catalyst locators/metadata only where required | Evidence-before-prose and citation verification |
| Local SQLite mode | Keep it as the fast deterministic test backend | Baseline tests and developer zero-credential workflow |
| React API contract | Deploy the same API and evidence payloads | Frontend data model and safety labels |

The rule for a proposed change is: first ask whether it can be implemented as
an adapter, port extension, container binding, or deployment wrapper. If yes,
do that. A core rewrite requires a recorded reason, a migration plan, and
parity tests before it is approved.

# What must not be claimed after Phase 2

Even after all Phase 2 gates pass, the following statements would still be
incorrect unless their later phases have separately passed:

- “The platform has live CCTNS/FIR read/write integration.”
- “The platform ingests real financial-system transactions.”
- “The platform predicts which named person will commit a future crime.”
- “The platform has validated causal socio-economic explanations.”
- “The local deterministic provider is a neural LLM.”
- “The platform is production-ready for police data.”

The accurate Phase 2 claim is:

> KSP-CIP is a Catalyst-validated Development deployment with Catalyst-backed
> relational/file persistence, replay-safe synthetic ingestion, deterministic
> data-quality and intelligence publication gates, evidence-bound API behavior,
> and a local fallback mode that remains available for regression testing.

# Final handoff checklist for Phases 0–2

- [ ] `docs/deployment/phase0-baseline.md` exists and is tied to a commit.
- [ ] `docs/deployment/catalyst-runtime.md` records the supported runtime and
      exact entrypoint contract.
- [ ] `docs/deployment/phase1-catalyst-runtime.md` records artifact, UI/API,
      readiness, secret, and rollback verification.
- [ ] `docs/deployment/catalyst-schema.md` records Catalyst table provisioning
      and schema version.
- [ ] `docs/deployment/phase2-data-plane.md` records live run IDs, parity,
      replay, DQ, export-security, and restore tests.
- [ ] Every deployment artifact has a hash and dependency manifest.
- [ ] Every Catalyst test is explicit, Development-scoped, and synthetic.
- [ ] No Firebase or parallel cloud service was introduced.
- [ ] No agent or domain service imports a Catalyst SDK directly.
- [ ] The previous artifact and last-good publication can be restored.
