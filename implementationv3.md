# KSP-CIP Implementation V3 — Catalyst Deployment-First Plan

## 1. Purpose and priority

Implementation V3 replaces V2 as the deployment execution plan. Its single
primary objective is to deploy KSP-CIP onto Zoho Catalyst and prove that the
deployed system works end-to-end with Catalyst services. V3 is not a feature
rewrite and it is not a second application architecture.

V3 preserves the working application core:

```text
React console → FastAPI → SupervisorAgent → five bounded agents
                                  ↓
       domain services, repositories, analytics, graph, evidence, audit
                                  ↓
   Catalyst adapters selected by the composition root and deployment settings
```

The deployment rule is strict: Catalyst must host the API, UI, functions,
scheduled work, relational data, object storage, session state, cache and
authentication. Firebase, Supabase, Redis, S3, another database, or another
cloud backend must not be introduced.

## 2. V3 branch ownership

V3 is developed only on:

```text
implementation-v3-catalyst-deployment
```

The separate non-deployment maintenance stream is developed on:

```text
implementation-v2.1-maintenance
```

### V3 owns

- `catalyst/` descriptors, AppSail services, functions, circuits and bootstrap;
- Catalyst Data Store, Stratus, NoSQL, Cache and Authentication bindings;
- deployment packaging and dependency artifacts;
- Catalyst environment configuration and secret names;
- schema provisioning/readiness checks;
- live batch/publication state needed to deploy safely;
- Catalyst health, telemetry, smoke tests, rollback and recovery;
- deployment documentation and release manifests.

### V2.1 owns

- application feature gaps that do not require changing the deployment shape;
- Bhashini language/voice behavior;
- forecast analytics and model evaluation;
- socio-economic and financial data contracts/features;
- multilingual retrieval/embedding quality;
- LLM input/output evaluation and product-quality fixes;
- minor local bugs, warnings, UI behavior and test improvements.

### Branch isolation rule

Do not develop the same file in both branches at the same time. If V2.1 needs
an interface owned by V3, first agree on a narrow contract, implement the
contract in one branch, and merge/cherry-pick that commit deliberately. V3
must not absorb unrelated product features while preparing the deployment.

## 3. Current status carried into V3

The detailed audit is in
[`docs/deployment/phase0-2-audit.md`](docs/deployment/phase0-2-audit.md). The
short, accurate status is:

| Previous phase | Completed and verified locally | Still required for Catalyst deployment |
|---|---|---|
| Phase 0 — baseline | Python 3.11+ local environment; deterministic seed; local API/evidence/scope tests; opt-in Catalyst test guards; rollback procedure; no-credential deterministic provider | Catalyst Development project, CLI/runtime inventory and live target configuration |
| Phase 1 — runtime foundation | AppSail API decision; Python runtime alignment; self-contained API/refresh artifacts; staged imports/execution; refresh handler validation; readiness/fail-fast configuration; identity boundary | Live AppSail/API/console/function deployment; exact stack acceptance; Node/npm build; proxy/CORS/UI smoke; live rollback |
| Phase 2 — data plane | Schema manifest; schema capability port; ZCQL escaping/pagination/upsert translation; Stratus adapter boundary; local run-control and DQ gate; Catalyst SDK isolation | Live Data Store/Stratus schema and parity; active publication pointer; DQ rollback; replay/timeout tests; export recovery; backup/restore |

### Current test evidence

The current local suite was run with a workspace-owned pytest temporary path:

```text
330 passed, 9 skipped, 10 warnings
```

The nine skips are guarded live-Catalyst tests. They are not evidence that
Catalyst is live. Node/npm and the Catalyst CLI are not available in the
current environment, so UI build and live CLI deployment remain open.

### What V3 may claim now

> KSP-CIP has a locally tested Catalyst deployment design and adapter layer.
> The Catalyst runtime and data-plane artifacts are prepared for live
> Development validation, but live deployment has not yet been proven.

V3 must not claim “deployed on Catalyst” until the V3 exit gate passes.

## 4. V3 deployment state machine

V3 must move through these states in order:

```text
LOCAL_BASELINE
  → CATALYST_PROJECT_READY
  → CATALYST_SCHEMA_READY
  → CATALYST_RUNTIME_READY
  → CATALYST_DATA_PLANE_READY
  → CATALYST_AUTH_READY
  → CATALYST_PIPELINE_READY
  → CATALYST_E2E_VERIFIED
  → CATALYST_PILOT_CANDIDATE
```

Each state has a report and evidence artifact. A failed state blocks the next
state; it is not marked complete by code inspection alone.

## 5. V3 Phase D0 — branch, baseline and deployment prerequisites

### Objective

Create a clean deployment branch and a reproducible record of the exact
application that will be deployed.

### Steps

1. Create `implementation-v3-catalyst-deployment` from the current approved
   baseline commit.
2. Do not stage `backend/var/`, generated landing files, local databases,
   `.env`, `.claude/` artifacts, credentials or exports.
3. Record the commit, Python, Node/npm and Catalyst CLI versions.
4. Install Node/npm and the approved Catalyst CLI on the deployment machine.
5. Create a separate Catalyst Development project/environment. Do not use
   Production for first deployment or mutating tests.
6. Create a deployment secret inventory containing names, owners, rotation
   dates and purpose—not secret values.
7. Confirm that the project has the required Catalyst services: AppSail,
   Event Functions, Circuit/scheduler, Data Store, Stratus, NoSQL, Cache and
   Authentication.
8. Record the region, project ID, environment name and service IDs in an
   ignored operator file and in the deployment report.

### Execute

From the repository root:

```powershell
git switch -c implementation-v3-catalyst-deployment
git rev-parse HEAD
python --version
node --version
npm --version
catalyst --version
```

Use the installed Catalyst CLI/console workflow for project initialization and
deployment; record the exact commands accepted by the installed CLI version in
`docs/deployment/v3-catalyst-commands.md`. Do not invent commands based on an
older CLI syntax.

### Verify

- [ ] V3 branch exists and starts from the approved baseline.
- [ ] No local database, generated NDJSON, secret or export is staged.
- [ ] Catalyst Development project is non-Production and service access is
      confirmed.
- [ ] CLI/runtime versions are recorded.
- [ ] Secret inventory and rollback owner are recorded.

### Deliverable

`docs/deployment/v3-phase-d0-baseline.md`.

## 6. V3 Phase D1 — build and validate deployment artifacts

### Objective

Produce self-contained, hashable deployment artifacts that do not depend on a
developer checkout or an implicit local path.

### Steps

1. Run `scripts/build_catalyst_artifact.py --target api` and `--target refresh`.
2. Build the React console with `npm ci`/the approved lockfile and `npm run
   build`.
3. Verify that the API and refresh artifacts contain the full `ksp_cip`
   package, schema/resources, bootstrap and target entrypoint.
4. Verify that the refresh artifact does not contain unused API dependencies.
5. Verify Python runtime compatibility with `backend/pyproject.toml`.
6. Verify AppSail API configuration, console configuration and function
   descriptors against the actual Catalyst project runtime identifiers.
7. Emit SHA-256 manifests for every artifact.
8. Run import/compile checks from staging directories with no repository
   `PYTHONPATH`.

### Required configuration

Set deployment selectors explicitly; do not rely on local defaults:

```text
KSPCIP_ENVIRONMENT=development
KSPCIP_DATASTORE_BACKEND=catalyst
KSPCIP_FILESTORE_BACKEND=catalyst
KSPCIP_KEYVALUE_BACKEND=catalyst
KSPCIP_CACHE_BACKEND=catalyst
KSPCIP_IDENTITY_BACKEND=catalyst
KSPCIP_CATALYST_PROJECT_ID=<secret/configured value>
KSPCIP_CATALYST_ENVIRONMENT=Development
KSPCIP_CATALYST_STRATUS_BUCKET=cip-ingest
```

The API must fail readiness if the system-of-record backend is selected but
its project/OAuth/schema configuration is missing. It must never silently fall
back to SQLite or local files in a Catalyst deployment.

### Verify

- [ ] API/refresh staging artifacts import in isolated mode.
- [ ] Artifact manifests and hashes are generated.
- [ ] React production bundle exists and contains no secrets.
- [ ] Descriptor stack names are accepted by the target project or have an
      approved correction recorded.
- [ ] Local defaults remain available only when the local environment is
      explicitly selected.

### Deliverable

`docs/deployment/v3-phase-d1-artifacts.md`, artifact manifests and build logs.

## 7. V3 Phase D2 — provision Catalyst schema and Stratus

### Objective

Provision the live data plane and prove that the same synthetic batch produces
the same authoritative records and derived evidence as SQLite.

### Data layout

```text
Catalyst Data Store
  curated organiser-schema tables
  cip_* control, audit, identity, graph, analytics and evaluation tables
  ext_* explicitly approved/synthetic extension tables

Stratus bucket: cip-ingest
  landing/{batch_id}/{source_table}.ndjson
  raw/{batch_id}/...
  manifests/{batch_id}.json
  exports/{user_id}/{session_id}/...
```

### Steps

1. Apply `docs/deployment/catalyst-schema-manifest.json` through the reviewed
   Catalyst provisioning workflow.
2. Create schema/version control and record manifest hash.
3. Create the `cip-ingest` bucket and restrict access to the application/service
   identity.
4. Configure landing, manifest and export prefixes.
5. Add/enable readiness checks for required tables, columns and schema version.
6. Upload one fixed synthetic seed manifest and NDJSON set.
7. Run ingest, DQ and intelligence refresh through the Catalyst function.
8. Compare SQLite and Catalyst primary keys, counts, aggregates, graph edges,
   alerts, embeddings, scores, evidence locators and publication version.
9. Run a query larger than the ZCQL page size and test quote/Unicode/null
   parameters.

### Required publication safety

Implement the active-publication mechanism before calling this phase complete:

- every run has an ID, batch/manifest hash and status;
- derived rows belong to a run/publication version;
- API reads select only the active version;
- promotion occurs after DQ and intelligence succeed; and
- failure leaves the prior active version queryable.

This must be implemented in pipeline/control/repository boundaries, not in the
agents or frontend.

### Verify

- [ ] Live schema matches the manifest.
- [ ] Same synthetic manifest has matching required IDs/counts/evidence in both
      backends.
- [ ] DQ failure does not change the active publication.
- [ ] Same-batch replay does not duplicate rows or edges.
- [ ] Stratus checksum, missing-object, ownership and expiry tests pass.
- [ ] No `PRAGMA`/`executescript` reaches Catalyst.

### Deliverable

`docs/deployment/v3-phase-d2-data-plane.md`, parity report and schema run ID.

## 8. V3 Phase D3 — deploy API, console and refresh orchestration

### Objective

Make all Catalyst runtime components reachable and correctly connected.

### Steps

1. Deploy `cip-api` as the approved Catalyst AppSail Python service running the
   existing FastAPI application factory.
2. Deploy `cip-console` AppSail with the built React assets.
3. Set `CIP_API_URL` to the API service origin and configure CORS for the
   console origin.
4. Deploy `cip_refresh` as the approved Event Function runtime.
5. Configure the Circuit/scheduler to call each stage explicitly:
   `ingest → data_quality → intelligence → retention`.
6. Ensure the event wrapper validates stage names, creates correlation IDs,
   records run status and returns the runtime's documented success/failure
   response.
7. Configure timeouts and memory based on measured seed/refresh durations.
8. Configure Catalyst logs and metrics without raw FIR text or credentials.

### Verify

- [ ] `/api/v1/health` is reachable through the deployed API.
- [ ] `/api/v1/health/ready` reflects Catalyst configuration/schema state.
- [ ] OpenAPI is reachable only according to the intended access policy.
- [ ] Console assets load from AppSail.
- [ ] Console `/api` calls reach the API without 502/protocol errors.
- [ ] Refresh function can execute each valid stage and rejects invalid stages.
- [ ] Circuit execution records run ID, stage, DQ and freshness.
- [ ] A failed stage is visible and retryable.

### Deliverable

`docs/deployment/v3-phase-d3-runtime.md`, service URLs/IDs, smoke output and
deployment logs.

## 9. V3 Phase D4 — enable Catalyst Authentication, NoSQL and Cache

### Objective

Replace local production dependencies with Catalyst-native services without
changing router or agent contracts.

### Authentication steps

1. Configure Catalyst Authentication and approved MFA/SSO/OIDC.
2. Map external subjects to `cip_user_account.external_subject`.
3. Verify issuer, audience, signature, expiry and actual Catalyst signing
   algorithm/JWKS behavior.
4. Resolve role, permissions and unit scope from the application mapping table.
5. Refuse unmapped, disabled, ambiguous and scope-less users.
6. Disable local/demo password login in the Catalyst environment.

### NoSQL steps

Bind `CatalystKeyValueStore` to `MemoryService` for session state, bounded
scratchpad, embedding cache and idempotency records. Qualify keys by user,
enforce TTL/document limits, and keep ordered transcripts in Data Store.

### Cache steps

Use Catalyst Cache only for replaceable masters, summary aggregates, query
embeddings and later TTS bytes. Invalidate on successful publication
promotion. A cache outage must become a miss/recompute, never an unavailable
answer or an authorization bypass.

### Verify

- [ ] Each role receives only its permitted scope.
- [ ] Session follow-up works across invocations and expires correctly.
- [ ] Another user cannot read session/scratchpad state.
- [ ] Cache loss/restart recomputes from Data Store.
- [ ] Publication promotion invalidates affected caches.
- [ ] Audit records remain authoritative and are not cached.

### Deliverable

`docs/deployment/v3-phase-d4-identity-state.md` and live access/TTL evidence.

## 10. V3 Phase D5 — end-to-end deployment validation

### Objective

Prove the complete deployed path with synthetic data before any pilot data is
considered.

### Test sequence

1. Login through Catalyst Authentication.
2. Run an investigator FIR lookup and verify unit scope.
3. Run an analyst aggregate/trend query.
4. Run a policymaker aggregate-only query.
5. Run a graph/network query and verify inferred markers.
6. Run a no-result query and verify trace/evidence.
7. Export a PDF and test owner/non-owner access.
8. Submit a new synthetic landing manifest.
9. Run Circuit ingest, DQ and intelligence stages.
10. Inject a blocking DQ defect and verify the old publication remains active.
11. Retry the same run and verify idempotency.
12. Exercise an API/runtime restart and confirm Data Store/Stratus state remains.
13. Check correlation IDs and safe logs for every request/job.

### Acceptance thresholds

- zero out-of-scope records, citations or graph nodes;
- zero missing required evidence locators;
- zero duplicate rows/edges after replay;
- zero public export objects;
- 100% DQ blocking behavior preserves last-known-good publication; and
- all fixed Phase 0 evaluation fixtures pass against the deployed API.

### Deliverable

`docs/deployment/v3-phase-d5-e2e.md` with request IDs, run IDs and screenshots or
sanitized response summaries.

## 11. V3 Phase D6 — security, recovery and pilot deployment gate

### Steps

1. Run secret scanning, dependency scanning, static analysis and API security
   tests against the exact release artifacts.
2. Confirm restrictive CORS, rate limits, upload limits and export ownership.
3. Verify append-only audit and retention/purge behavior.
4. Back up/export curated/control/landing data through approved Catalyst paths.
5. Restore synthetic data into an isolated Development project and rebuild
   derived intelligence.
6. Exercise deployment rollback and active-publication rollback.
7. Run a role-based UAT for investigator, analyst, supervisor, policymaker,
   auditor and administrator.
8. Record RPO/RTO, measured API/Circuit performance, open risks and owners.
9. Freeze the exact artifact, schema, configuration and provider versions in a
   release manifest.
10. Promote only after owner/policy/security approval.

### V3 final exit gate

- [ ] Catalyst Development API, console, refresh function and Circuit are live.
- [ ] Data Store/Stratus are authoritative and parity-tested.
- [ ] Schema readiness and active publication rollback are enforced.
- [ ] Catalyst Auth, NoSQL and Cache are live and scope-safe.
- [ ] Full synthetic E2E corpus passes on deployed services.
- [ ] DQ failure, replay, timeout, export ownership and recovery tests pass.
- [ ] Security, rate limits, secrets, audit and retention are verified.
- [ ] UAT and rollback are signed off.
- [ ] The release manifest contains artifact hashes and no secret values.
- [ ] No Firebase or parallel backend was introduced.

**V3 completion statement:**

> KSP-CIP is deployed and verified in a Catalyst Development environment for
> the approved synthetic corpus, with Catalyst-backed API/UI, persistence,
> files, identity, state, cache and pipeline execution. Production/pilot data
> remains disabled until the separate governance and V2.1 capability gates are
> approved.

## 12. V3 operational commands and evidence

The deployment operator must record exact accepted commands in
`docs/deployment/v3-catalyst-commands.md`. At minimum, the runbook must include:

```text
build API artifact
build refresh artifact
build console
validate artifact imports/manifests
deploy/update API AppSail
deploy/update console AppSail
deploy/update refresh function
deploy/update Circuit
run readiness smoke
run mutating synthetic smoke
pause/restore Circuit
rollback artifact
restore last-good publication
```

The commands are intentionally not hard-coded here because Catalyst CLI syntax
and the accepted stack identifiers must be confirmed against the installed CLI
and target region. The runbook must preserve the exact command/output pair.

## 13. V3 branch workflow

```powershell
git switch implementation-v3-catalyst-deployment
git pull --ff-only origin implementation-v3-catalyst-deployment
# make only deployment-scoped changes
git add catalyst docs/deployment scripts implementationv3.md
git commit -m "Deploy KSP-CIP on Catalyst"
git push -u origin implementation-v3-catalyst-deployment
```

Before each push:

- inspect `git diff --cached`;
- ensure no `backend/var`, `.env`, secret, export or generated landing file is
  staged;
- run local tests and artifact builds; and
- attach the deployment report/run IDs to the commit or release record.

## 14. V3 does not absorb these V2.1 tasks

The following remain on `implementation-v2.1-maintenance` and must not be
implemented opportunistically in the deployment branch:

- full Bhashini translation/ASR/TTS product behavior;
- aggregate forecast model development and backtesting;
- approved socio-economic reference integration;
- real financial ingestion;
- multilingual embedding model rollout;
- Level C hosted-LLM evaluation;
- unrelated UI refinements and analytics enhancements.

V3 may expose the deployment seams and feature flags required by these tasks,
but it must not make them appear complete merely because Catalyst can host them.
