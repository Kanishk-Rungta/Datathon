# Phase 1 report — Catalyst runtime, packaging and deployment foundation

Companion to `implementationv2-phases-0-2.md` Phase 1. No live Catalyst
project exists in this environment (no CLI, no project id, no credentials),
so this phase's exit gate is split honestly: what is verified locally, and
what genuinely requires a live project and is therefore still open.

## P1-01 — Runtime shape decision

**Decision:** host the FastAPI application as a Catalyst AppSail Python
service running `uvicorn`, not as an Advanced I/O function. Full reasoning
and sources: [catalyst-runtime.md](catalyst-runtime.md).

The defect this replaced: the inherited `cip_api` Advanced I/O function's
handler was `def handler(context, basicio): return app` — it returned the
ASGI application object to a runtime with no documented way to invoke one.
Zoho's own docs describe Advanced I/O as adding native request/response
objects over Basic I/O's `basicio.write()`/`getArgument()` primitives; neither
is an ASGI bridge. AppSail Python, by contrast, is documented with working
framework examples for exactly this shape of problem.

## P1-02 — Runtime version alignment

- `catalyst.json` and `cip_refresh/catalyst-config.json`: `python3.9` →
  `python3.11`, matching `backend/pyproject.toml`'s `requires-python>=3.11`.
- The new `appsail/api/app-config.json` declares `python3_11`.
- **Not verified against a live project**: the exact stack identifier string
  Catalyst's current CLI/console accepts (Zoho's own Flask example showed
  `Python_3_9`, capitalized, underscored — a different convention from the
  function-descriptor style this repo already used). Confirm at provisioning
  time; both files carry an explicit comment saying so.
- Node 18 for `cip-console` is unchanged (no live verification possible; no
  Node/npm installed in this environment either).

## P1-03 — Self-contained artifact

`scripts/build_catalyst_artifact.py` stages `ksp_cip/` (the full package,
including `resources/prompts`, `resources/lexicon`, and
`infrastructure/db/schema.sql` — all already inside the package tree, so no
separate copy step was needed for them), the shared `_bootstrap.py`, and the
target-specific entrypoint/config/requirements into `dist/cip-<target>/`.

It then **verifies** self-containment rather than asserting it: it imports
`ksp_cip` (and, transitively, `ksp_cip.interface.container` and
`ksp_cip.interface.api.main`) in a subprocess run with `python -I` (isolated
mode — no `PYTHONPATH`, no user site-packages) and `sys.path` limited to the
staging directory. A repo-relative import that "worked" only because the
script happened to run from inside the checkout would fail this check.

Run and verified locally for both targets:

```text
python scripts/build_catalyst_artifact.py --target api
  -> OK: ksp_cip imports with sys.path limited to the staging directory
  -> 198 files, ~1.69 MB, manifest at dist/cip-api.manifest.json

python scripts/build_catalyst_artifact.py --target refresh
  -> OK: ksp_cip imports with sys.path limited to the staging directory
  -> 198 files, ~1.70 MB, manifest at dist/cip-refresh.manifest.json
```

Both staged entrypoints were then **executed** (not just imported) directly
out of their staging directories, with only that directory on `sys.path`,
against the local SQLite backend:

- `dist/cip-refresh`: `main.run_stage("data_quality")` ran end-to-end,
  including the new run-control wrapper (correlation id, batch registration,
  success/failure marking).
- `dist/cip-api`: `server.py` bound `0.0.0.0` on the port named by
  `X_ZOHO_CATALYST_LISTEN_PORT`, built the container, warmed reference caches,
  and served `GET /api/v1/health` with a 200.

The old, structural defect this fixes: entrypoints previously computed
`Path(__file__).resolve().parents[N] / "backend"`, which only resolves inside
a checkout. `_bootstrap.py` now checks for a `ksp_cip` **sibling** of the
entrypoint first (the staged layout) and falls back to the repo-relative path
only for running an entrypoint directly out of `catalyst/` during
development — both paths are exercised by the tests above.

## P1-04 — Configuration and fail-fast behavior

Already substantially built in the prior Catalyst-adapters pass
(`config/settings.py`'s five independent backend selectors and
`deployment_problems()`). This phase's addition:

- **The `KSPCIP_ENVIRONMENT=catalyst` defect.** `Environment` has no
  `catalyst` member (`local`/`development`/`staging`/`production`); the old
  entrypoints would have raised a `pydantic` validation error constructing
  `Settings()` before the app even started. Fixed in `_bootstrap.py`:
  `KSPCIP_ENVIRONMENT` now defaults from `KSPCIP_CATALYST_ENVIRONMENT`
  (`Production` → `production`, anything else → `development`), and only as a
  default — an operator's explicit `KSPCIP_ENVIRONMENT` is never overridden.
  Verified: constructing `Settings()` with only `KSPCIP_CATALYST_ENVIRONMENT`
  set no longer raises, and an explicitly-set `KSPCIP_ENVIRONMENT=staging` is
  preserved (both exercised by the local server run above and by
  `test_catalyst_adapters.py`'s deployment-validation tests).
- `DATASTORE_BACKEND=catalyst` + `FILESTORE_BACKEND=local` remains refused
  (prior pass); local directory creation (`ensure_directories()`) is
  unconditional today but harmless on a read-only filesystem since it only
  runs for the local SQLite/file-store defaults' own paths — not exercised
  further this phase.

## P1-05 — Health and readiness

`GET /api/v1/health` (liveness) is unchanged in contract — process up,
self-description, existing `TestHealthAndCapabilities` tests still pass.

`GET /api/v1/health/ready` now additionally reports `configuration_valid`,
`configuration_problems` (from the same `deployment_problems()` fail-fast
check the container itself enforces at startup — so the same problem is
visible without waiting for a crash), and `degraded_optional_services` (today:
the offline Kannada glossary fallback, a genuine known limitation — the local
deterministic LLM is deliberately *not* listed as degraded, since it is the
platform's trustworthy default per ADR-0003, not a fallback from a broken
service). `ready` is `false` when configuration is invalid or the store is
unreachable; it stays `true` when only an optional provider is degraded, per
P1-05's instruction that readiness fails for the system-of-record, not for an
optional dependency.

No secret, prompt text, FIR content, or raw provider response appears in
either payload — verified by inspection of `health.py` and by the existing
secret-hygiene discipline in `Settings.deployment_problems()`.

## P1-06 — Refresh entrypoint runtime safety

`cip_refresh/main.py`'s `run_stage()` now: validates the stage name against
the four known stages before doing anything else; generates a correlation id;
registers a durable run-start record in `ctl_batch_log` before the stage body
runs; marks the record `FAILED`/`LOADED` in a `try`/`except`/`else`; and
returns the correlation id and duration alongside the stage's own result. The
event-wrapper `handler(event, context)` shape itself needed no change — it
already matched Zoho's documented Event Function contract.

Verified locally: a valid stage runs and updates the control table; an
invalid stage name raises before any container work happens; `handler()`
round-trips a dict payload correctly (all three exercised directly against
the seeded local database).

## P1-07 — Console build and proxy

**Not exercised.** Node.js and npm are not installed in this environment, so
`npm ci`, the production build, and serving `frontend/dist` through AppSail
could not be run or verified this phase. This is stated as an open item, not
silently skipped.

What *was* done without a Node toolchain: hardened `cip-console/server.js`'s
`/api` proxy to select the `http` or `https` client module based on the
target URL's own protocol, rather than always assuming `https` — an
AppSail-to-AppSail internal call is not guaranteed to be TLS the way a public
function URL was, and `CIP_API_URL` now points at the new `cip-api` AppSail
service. This is a small, reviewable code change; it has not been exercised
against a running console because there is no local Node runtime to run one.

## P1-08 — Dependency and secret checks

- `cip_refresh/requirements.txt` trimmed: `fastapi` and `python-multipart`
  removed — verified by static import scan that `build_container()` never
  imports `ksp_cip.interface.api`, so the refresh function never needed them.
- No runtime code imports `httpx` or `requests` — confirmed by repo-wide grep;
  every HTTP-calling adapter (Bhashini, LLM providers, both Catalyst adapters)
  uses the standard library's `urllib`. `httpx` remains a dev/test-only
  dependency (the test client), correctly absent from every deployment
  artifact's `requirements.txt`.
- Secret scan (pattern match for `key`/`secret`/`token`/`password` literals)
  run over every new/changed file before commit; none found.
- `dist/` (build output) is gitignored; nothing under it is committed.

## P1-09 — Identity boundary

Unchanged this phase; already established in the prior Catalyst-adapters pass
(`CatalystIdentityProvider` behind the same `Principal` contract,
`identity_backend` selector). Confirmed still true: no router or agent
imports anything from `infrastructure.catalyst.identity` — only
`interface/container.py`'s `_build_identity` factory does.

## Exit gate

- [x] Runtime and descriptors aligned with Python 3.11+ (stack-identifier
      string itself still needs live confirmation — noted above).
- [x] API and refresh artifacts are self-contained and hash-manifested
      (`scripts/build_catalyst_artifact.py`, verified for both targets).
- [ ] The API entrypoint has been tested against the **actual** Catalyst
      runtime contract — not possible without a live project; verified
      instead against the documented contract and by running the staged
      artifact locally end-to-end.
- [ ] The deployed UI loads a production build through the configured proxy
      — not exercised; no Node/npm in this environment.
- [x] Catalyst configuration cannot silently select local file storage,
      development JWTs, or missing provider credentials
      (`deployment_problems()`, exercised by both the local server run above
      catching the JWT-placeholder case and by the existing unit tests).
- [x] Health/readiness and correlation IDs are visible without sensitive data.
- [x] The Phase 0 query/evidence corpus's factual-path assertions are already
      covered by the automated suite running against this same code; a subset
      (health, login, trend, empty-result, scope) was additionally re-run live
      against the AppSail entrypoint specifically, not just the dev server.
- [ ] Deployment and rollback runbooks against a live project — not
      exercised; the rollback procedure in `phase0-baseline.md` covers the
      code/config level, which is everything reachable without a project.

**Exit artifact:** this document, `dist/cip-api.manifest.json`,
`dist/cip-refresh.manifest.json` (regenerate via
`scripts/build_catalyst_artifact.py`; not committed — build output), and the
updated test suite (339 tests collected, 330 passing locally, 9 skipped by
design pending live credentials).

---

## Superseded, 29 Aug 2026

Two statements above are no longer accurate. Both are corrected in
`docs/deployment/catalyst-runtime.md` (addendum) and
`docs/deployment/v3-phase-d1-artifacts.md` (addendum); noted here so this file
is not read on its own and believed.

1. **"verified for both targets" is now three targets.** `cip-console` had the
   same non-self-contained defect the Python artifacts were fixed for: its
   `server.js` read the console build from outside the directory Catalyst
   ships. There is now a `--target console`, and `catalyst.json` records
   `deploy_source` and `build` per component.
2. **The self-containment check was over-broad, in a way that hid a
   requirements mismatch.** It imported `ksp_cip.interface.api.main` for the
   *refresh* target too, proving a FastAPI dependency that function's own
   `requirements.txt` deliberately omits. Import lists are now per-target.

Also on this path: `_bootstrap.py` defaulted the data store to Catalyst while
leaving the file store on `local` — a pair `deployment_problems()` rejects, so
every first deploy would have failed at startup. Both are defaulted together
now, and `tests/unit/test_catalyst_bootstrap.py` asserts that whatever the
bootstrap defaults is a combination the validator accepts.

Current suite: **581 passing, 9 skipped.**
