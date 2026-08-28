# Phase 0–2 implementation audit

## Audit purpose

This report checks the repository against the exit gates in
[`implementationv2-phases-0-2.md`](../../implementationv2-phases-0-2.md). It
separates code and local-test evidence from evidence that can only come from a
real Zoho Catalyst Development project. A descriptor, adapter, or skipped test
is not treated as proof of a live deployment.

## Audit execution

The repository was inspected at the current working tree. The local suite was
run with a workspace-owned pytest temporary directory because the default
Windows temporary directory is not writable in this environment:

```text
Python: 3.13.13
Command: .venv\Scripts\python.exe -m pytest -q --basetemp .pytest-tmp
Result: 330 passed, 9 skipped, 10 warnings
```

The nine skipped tests are the explicitly opt-in live-Catalyst tests. They
require a project ID, OAuth credentials, a non-Production Catalyst environment,
and the smoke-test flag. No Catalyst project, Catalyst CLI, Node.js, or npm is
available in this environment, so no live network claim is made.

## Summary status

| Phase | Local implementation | Live Catalyst evidence | Overall status |
|---|---|---|---|
| Phase 0 — baseline | Baseline report, seed, API smoke paths, safety corpus, opt-in test guards and rollback documentation exist | No Catalyst project is provisioned | **Locally complete; Catalyst setup pending** |
| Phase 1 — runtime/deployment foundation | AppSail API decision, Python alignment, self-contained artifacts, refresh wrapper, readiness checks and fail-fast configuration are implemented and locally exercised | API runtime contract, deployed API, AppSail console build/proxy and rollback are not live-tested; Node/npm are unavailable | **Partially complete** |
| Phase 2 — Catalyst data plane | Schema manifest, schema capability port, ZCQL upsert translation, parameter escaping, pagination code, Stratus adapter and local run-control wrapper exist | Live Data Store/Stratus parity, active publication pointer, DQ rollback, live replay, export recovery and backup/restore are not verified | **Partially complete; highest deployment risk** |

## Phase 0 findings

### Verified

- `docs/deployment/phase0-baseline.md` records a frozen local baseline,
  Python version, dependency freeze, seed result, query/evidence scenarios,
  local-provider status and rollback procedure.
- The local synthetic pipeline and API are working. The current run passed the
  complete suite with 330 tests passing; the nine live-Catalyst tests skipped
  for lack of explicit credentials and opt-in.
- The deterministic local provider is correctly described as an offline
  LLM-compatible fallback, not neural model inference.
- The evaluation corpus covers routing, scope, evidence, prompt injection,
  sensitive attributes, financial permissions, causal overclaiming and
  individual future-crime requests.
- Deployment readiness and mutating smoke tests fail closed without explicit
  Development configuration.

### Still open

- A Catalyst Development project has not been provisioned.
- The baseline has no live Catalyst health, Data Store, Stratus, or UI result.
- Node/npm and Catalyst CLI are not installed, so frontend artifact and CLI
  deployment evidence is absent.

### Phase 0 conclusion

Phase 0 is complete as a **local engineering baseline**. It is not evidence
that Catalyst has been configured. The first task of the next phase is to
provision a disposable Development project and execute the guarded smoke tests.

## Phase 1 findings

### Verified

- The deployment shape was changed from the unverified Advanced I/O FastAPI
  shim to a Catalyst AppSail API service running the existing FastAPI factory.
- Function/runtime descriptors were moved from Python 3.9 to a Python 3.11
  declaration consistent with `backend/pyproject.toml`.
- `scripts/build_catalyst_artifact.py` builds API and refresh staging artifacts,
  copies the complete `ksp_cip` package and resources, emits manifests, and
  imports from an isolated staging path.
- Staged API and refresh artifacts were executed locally against SQLite.
- `_bootstrap.py` supports staged and repository-local layouts.
- Catalyst environment-name mapping avoids the previous invalid
  `KSPCIP_ENVIRONMENT=catalyst` value.
- Health/readiness exposes safe configuration and degraded-optional-service
  state without case text or secrets.
- Refresh stage validation, correlation IDs, run-start registration and bounded
  failure marking are implemented.
- Deployment requirements do not include unused HTTP dependencies; current
  runtime adapters use the standard library HTTP client.
- Catalyst identity remains behind the existing `Principal` boundary.

### Still open

- The exact Catalyst AppSail Python stack identifier has not been accepted by a
  live project.
- The deployed API request path has not been exercised.
- `frontend/dist` has not been built or served because Node/npm are unavailable.
- AppSail proxy behavior, `CIP_API_URL`, deployed CORS and UI-to-API routing
  have not been verified.
- Live deployment rollback has not been exercised.

### Phase 1 conclusion

Phase 1 is **locally implemented but not deployment-complete**. It can proceed
to live validation, but it must not be reported as a working Catalyst service
until the runtime, API, UI and rollback checks pass in a Development project.

## Phase 2 findings

### Verified

- `scripts/generate_schema_manifest.py` creates a reviewed provisioning manifest
  from `schema.sql` and forward migrations.
- The `DataStore` port has `table_columns`; loader metadata inspection no longer
  sends raw `PRAGMA` from application code.
- Catalyst schema reflection includes migration-added columns and tables; the
  local parity suite covers the manifest/parser against SQLite metadata.
- `CatalystDataStore` handles scalar escaping, refuses raw `PRAGMA`, translates
  supported `ON CONFLICT` statements to read-then-write behavior, and paginates
  provider responses.
- The Stratus adapter implements the file-store boundary and key validation.
- Existing local DQ behavior blocks the refresh path before intelligence is
  published when a blocking check fails.
- Pipeline entrypoints have durable invocation registration and correlation IDs.
- Agents, repositories and domain services remain independent of Catalyst SDK
  imports; binding occurs at the container/infrastructure boundary.

### Still open and blocking a full Phase 2 claim

1. **No live schema check:** the manifest exists, but a Catalyst readiness check
   has not verified the actual table/column/version state.
2. **No live Data Store parity:** the same landing manifest has not been loaded
   into Catalyst and compared with SQLite IDs, counts, graph edges, alerts,
   embeddings and evidence locators.
3. **No publication-version state machine:** rows are not consistently tagged
   with a run/publication version and switched through a last-known-good active
   pointer. A pre-refresh DQ gate exists, but the stronger old-version-remains-
   visible guarantee is not implemented.
4. **No live Stratus round trip:** upload, checksum mismatch, owner access,
   signed/application download, retention and recovery are not exercised.
5. **No live failure injection:** provider timeout, retry-after-write, partial
   load, DQ failure, refresh failure and replay behavior are not tested on the
   Catalyst backend.
6. **No backup/restore drill:** curated/control/landing data has not been
   restored to an isolated Catalyst Development project.
7. **Concurrency limitation remains:** Catalyst upsert emulation is
   read-then-write and not atomic. It is acceptable only for deterministic,
   replayable keys; it must not be used for counters or unguarded concurrent
   writers.

### Phase 2 conclusion

Phase 2 is **code-and-contract complete for the unblocked portion**, but its
operational Catalyst portion is incomplete. The remaining work is not a reason
to rewrite repositories; it requires a live Development project, publication
state changes at the data/pipeline boundary, and live parity/recovery evidence.

## Required prerequisite before Phase 3

Phase 3 may be designed now, but live Phase 3 execution must begin with a
Phase 2 closeout sprint:

- install Node/npm and the approved Catalyst CLI;
- provision a non-Production Catalyst Development project;
- create Data Store tables from the reviewed manifest;
- create the `cip-ingest` Stratus bucket and ownership prefixes;
- configure OAuth/secrets through Catalyst configuration; and
- run the guarded readiness and mutating smoke suites.

Until those actions are complete, the product status should say:

> Local implementation and Catalyst adapters are tested; Catalyst deployment
> remains unverified and the live data-plane publication/recovery gate is open.

## Audit evidence files

- [Phase 0 baseline](phase0-baseline.md)
- [Phase 1 report](phase1-catalyst-runtime.md)
- [Phase 2 report](phase2-data-plane.md)
- [Catalyst runtime decision](catalyst-runtime.md)
- [Catalyst schema process](catalyst-schema.md)
- [V3 Catalyst deployment plan](../../implementationv3.md)
