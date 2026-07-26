# Phase 0 baseline report

Companion to `implementationv2-phases-0-2.md` §Phase 0. Records a known-good
local reference state that every later deployment change can be compared
against and rolled back to.

## P0-01 — Frozen baseline

| Field | Value |
|---|---|
| Commit | `3e07b79498e4bc3fd04afb04f69c11f36e3ceb9a` |
| Branch | `feature/v2-catalyst-adapters-and-analytics` |
| OS | Windows 11, build 10.0.26200 |
| Python | 3.13.13 (repository requires `>=3.11`; satisfied) |
| Node.js | **not installed** on this machine |
| npm | **not installed** on this machine |
| Catalyst CLI | **not installed** on this machine |

Dependency versions: [phase0-pip-freeze.txt](phase0-pip-freeze.txt) (`pip freeze`
inside `.venv`, generated at this commit).

**Current known limitations, stated plainly** (unchanged by this phase):
local SQLite datastore by default, local file store by default, local/demo
identity by default, `LocalDeterministicProvider` as an offline
LLM-interface-compatible fallback — **not a running neural model** — and
synthetic data only. No live Catalyst project has been exercised.

No `.env`, OAuth token, LLM key, Bhashini key, Catalyst secret, signed URL, or
generated case export is included in this commit. `backend/var/filestore/`
contents are gitignored; the tracked `backend/var/ksp_cip.db` is the
synthetic demo database shipped for local convenience, not a report artifact.

## P0-02/03 — Toolchain and local run

```text
python -m compileall backend/ksp_cip       -> exit 0
python -m pytest backend/tests -q          -> 311 passed, 5 skipped, exit 0
python -m ksp_cip.cli seed --cases 100 --months 6 --reset  -> completed, 2.91s
```

Skips are the opt-in live-Catalyst smoke tests
(`backend/tests/integration/test_deployment_smoke.py`), which refuse to run
without explicit credentials — see P0-06 below.

Seed summary (100 cases / 6 months, seed reset):

- 100 cases generated and loaded; 0 data-quality blocking failures.
- Graph: 252 nodes / 313 edges (`CO_ACCUSED` 64, `ALLEGED_IN` 120,
  `MONEY_FLOW` 82 synthetic, `REPEAT_OFFENDER` 10, `SAME_MODUS_OPERANDI` 8,
  `ARRESTED_BY` 27, `SAME_LOCATION` 2).
- Entity resolution: 11 auto-linked pairs, 0 in the review band at this scale.
- 200 retrieval documents indexed.
- 3 synthetic reference events seeded (Dasara, Deepavali, year-end gathering).
- `language_provider: local-lexicon`, `language_full_fidelity: false`,
  `llm_provider: local` — all as expected for the zero-credential default.

## P0-04 — Query and evidence matrix

Exercised against `uvicorn ksp_cip.interface.api.main:get_app --factory` on
`127.0.0.1:8123`, the 100-case seed above. Correlation IDs and exact response
bodies are not reproduced here (they are not report artifacts); pass/fail and
the evidence property checked are.

| Scenario | Method | Result |
|---|---|---|
| Health | `GET /api/v1/health` | 200; reports `sqlite`/`local`/`local` backends, `cases: 100`, `seeded: true` |
| OpenAPI docs | `GET /api/v1/docs` | 200 |
| Login | `POST /api/v1/auth/login` (`analyst.state`) | 200; JWT issued, principal + scope returned |
| Statewide analyst trend query | `POST /api/v1/chat` "What is the crime trend this year?" | `TREND_QUERY`, every numeric claim carries an `AGG:` locator, `ComputationTrace` present |
| Empty result | `POST /api/v1/chat` "Show me murder cases in Kodagu registered in 1998" | Honest "No FIR... matches" claim; trace still records the query (district/sub-head/scope), zero rows is itself evidenced by the trace |
| Investigator scope vs analyst scope | `POST /api/v1/chat` "Show me all recent cases" as `io.bengaluru` vs `analyst.state` | Investigator: 4 FIRs, all at Bengaluru City Market PS. Analyst: 100 (statewide). Narrower scope confirmed by row count and station name, not by trusting the client |
| Policymaker aggregate-only | Not re-run this pass; covered by `TestRoleBasedAccess.test_policymaker_cannot_read_case_detail` in the automated suite | covered by automated suite |
| Inferred graph link marker | Not re-run this pass; covered by `TestAnswerContract.test_inferred_claims_are_marked` | covered by automated suite |
| Synthetic financial marker | Not re-run this pass; covered by ADR-0005 tests and `network_intelligence` agent tests | covered by automated suite |
| LLM disabled path | `llm_provider: local` for this entire run — every query above went through the deterministic path with no live provider | confirmed by the health payload |
| PDF export ownership | Not re-run this pass; covered by `TestExport.test_a_user_cannot_read_another_users_export` | covered by automated suite |
| DQ blocking failure | Not re-run this pass; covered by `TestPipelineAndData` integration suite | covered by automated suite |
| Repeat seed / no duplicates | Not re-run this pass; covered by `test_pipeline_and_data.py` idempotency assertions | covered by automated suite |

Rows marked "covered by automated suite" are exercised by the existing 311-test
suite on every run rather than re-verified manually here; re-running them by
hand would duplicate, not strengthen, the evidence. The rows above the line
were exercised live against a running server for this report specifically,
because they are the ones that most depend on request/response wiring rather
than pure application logic.

## P0-05 — Catalyst Development configuration (documented, not live)

No Catalyst project has been provisioned for this phase. The settings below
are the complete list this build needs before selecting any Catalyst backend
(see [../deployment.md](../deployment.md) for the full reference and
[catalyst-runtime.md](catalyst-runtime.md) for the runtime decision):

```text
KSPCIP_ENVIRONMENT               (local | development | staging | production)
KSPCIP_DATASTORE_BACKEND         (sqlite | catalyst)
KSPCIP_FILESTORE_BACKEND         (local | catalyst)
KSPCIP_KEYVALUE_BACKEND          (relational | catalyst)
KSPCIP_CACHE_BACKEND             (memory | catalyst)
KSPCIP_IDENTITY_BACKEND          (local | catalyst)
KSPCIP_CATALYST_PROJECT_ID
KSPCIP_CATALYST_ENVIRONMENT      (Development | Production)
KSPCIP_CATALYST_BASE_URL
KSPCIP_CATALYST_OAUTH_CLIENT_ID
KSPCIP_CATALYST_OAUTH_CLIENT_SECRET
KSPCIP_CATALYST_OAUTH_REFRESH_TOKEN
KSPCIP_CATALYST_STRATUS_BUCKET
```

For Phase 0 every backend selector remains at its local default. Nothing here
is claimed live.

## P0-06 — Opt-in deployment smoke tests

Two things exist and are deliberately layered rather than duplicated:

1. `backend/tests/integration/test_deployment_smoke.py` — mutating,
   Catalyst-project-specific smoke tests (upsert round-trip, Stratus round-trip).
   Gated by `KSPCIP_SMOKE_ENABLED=1` **and** a project id **and** OAuth
   credentials **and** a non-`Production` `KSPCIP_CATALYST_ENVIRONMENT`.
2. A `deployment` pytest marker (added this phase — see
   `backend/tests/integration/test_deployment_readiness.py`) for **read-only**
   Development-scoped checks (health/readiness reachability), gated by
   `KSPCIP_RUN_CATALYST_TESTS=1` plus the same project/environment guard, kept
   separate from (1) because it must never write.

Both fail closed: absent any one condition, the tests skip with a stated
reason and never infer a default project.

## P0-07 — Rollback point

Tested procedure:

```bash
git status --short                 # confirm nothing uncommitted is lost
git checkout 3e07b79498e4bc3fd04afb04f69c11f36e3ceb9a -- .   # or checkout the tag/branch
# unset any KSPCIP_*_BACKEND overrides in the shell/session
cd backend && ../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe -m ksp_cip.cli seed --cases 100 --months 6 --reset
```

No production or user-facing data is deleted by this phase; the only mutable
state is the local `backend/var/` SQLite database and file store, both
disposable and regenerated by `seed --reset`.

## Exit gate

- [x] Clean local setup succeeds on Python 3.11+ (3.13.13 here).
- [x] `compileall` passes.
- [x] Full local test suite passes (311 passed, 5 skipped by design).
- [x] A small seed creates the expected curated and intelligence records.
- [x] API health, OpenAPI, authentication, FIR retrieval, analytics, empty-result
      paths exercised live; graph/PDF/DQ/replay paths covered by the automated
      suite (see table above).
- [x] The LLM provider is the local deterministic fallback for this entire run;
      no live provider was consulted for any factual payload.
- [x] No secret, token, sensitive export, or real case data is tracked (verified
      by `git status` and a pattern scan before commit).
- [x] Catalyst Development target values and opt-in smoke-test guard are
      documented (P0-05, P0-06).
- [x] Rollback to the baseline commit is a tested, three-command procedure.

**Node/npm/Catalyst CLI are not installed on this machine.** Frontend build
verification and any live Catalyst CLI operation are therefore out of scope for
this report and remain explicitly open — see `catalyst-runtime.md` and
`phase1-catalyst-runtime.md` for how that gap is carried forward.
