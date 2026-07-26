# KSP-CIP Implementation V2.1 — Pending Features, Blockers and Quality Fixes

## 1. Purpose

Implementation V2.1 is the maintenance and feature-completion plan that
remains after the deployment work was separated into
[`implementationv3.md`](implementationv3.md). V2.1 is intentionally not a
second deployment plan. Its job is to complete, evaluate and govern the
features that V2 specified but that are not required to establish the first
Catalyst deployment.

V2.1 is developed only on:

```text
implementation-v2.1-maintenance
```

The deployment branch is:

```text
implementation-v3-catalyst-deployment
```

Do not develop the same deployment descriptor, Catalyst adapter or release
configuration in both branches. V2.1 may add a narrow application port or
feature flag when V3 needs it, but the change must be reviewed and deliberately
cherry-picked rather than copied informally.

## 2. What V2 already completed

The repository contains substantial completed work. V2.1 must not rewrite it:

- deterministic FIR/case retrieval, evidence and computation traces;
- trends, hotspots, early-warning anomaly detection, seasonality and approved
  event-window comparison;
- guarded demographic/sociological aggregates and small-cell suppression;
- five bounded agents and deterministic supervisor routing;
- reversible entity resolution, graph analysis and synthetic money-flow
  extension;
- recorded-history/case-priority scoring that is not individual prediction;
- ACL-prefiltered retrieval and local deterministic embeddings;
- English and limited offline Kannada terminology support;
- PDF export, audit trail, role/scope enforcement and problem responses;
- Catalyst adapter seams, local contract tests and the local fallback provider;
- entity-resolution calibration suite;
- Level A/B agent evaluation corpus and factual-diff harness; and
- local SQLite pipeline, DQ checks, idempotent replay tests and schema manifest.

The current local test result is 330 passing tests and 9 intentionally skipped
live-Catalyst tests. The skipped network tests are deployment work owned by V3,
not evidence of a V2.1 feature failure.

## 3. V2.1 status categories

| Category | Meaning | Action |
|---|---|---|
| Pending | Code boundary exists but feature behavior is incomplete | Implement behind the existing port/agent/API seam |
| Blocked | Requires approval, provider credentials, live Catalyst or governed data | Prepare adapter/tests/docs; do not fake completion |
| Hardening | Existing feature works locally but needs quality, security or scale work | Add regression tests and measured improvements |
| Deferred | Explicitly prohibited or outside the approved scope | Do not implement |

## 4. Workstream ownership rules

V2.1 may modify:

- `application/analytics/`, `application/agents/` and feature-specific ports;
- language/embedding/LLM adapters and evaluation tooling;
- feature routers/schemas and corresponding React components;
- synthetic generators and test fixtures;
- model cards, data contracts and governance reports.

V2.1 must not directly modify unless coordinated with V3:

- Catalyst descriptors, AppSail deployment shape or runtime stacks;
- Catalyst OAuth/secret naming and deployment manifests;
- Catalyst Data Store/Stratus/NoSQL/Cache adapter behavior;
- active publication and deployment rollback implementation;
- production CORS, service routing or branch release configuration.

If a feature requires a new Catalyst table, Stratus prefix or Circuit stage,
V2.1 specifies the contract and local implementation; V3 owns the live
provisioning and deployment commit.

---

# V2.1 Phase M0 — establish the maintenance baseline

## Objective

Create the maintenance branch, freeze the current functional behavior and make
every pending feature measurable before changing code.

## Steps

1. Create `implementation-v2.1-maintenance` from the approved current baseline.
2. Run the local suite with a workspace-owned pytest temporary directory.
3. Save the current agent evaluation corpus result and factual signatures.
4. Record the current API route/schema inventory and UI feature inventory.
5. Identify whether a task is local-complete, provider-blocked, governance-
   blocked or deployment-owned.
6. Add regression fixtures before changing behavior.

## Verification

- [ ] All current local tests pass.
- [ ] Existing query facts, locators, scope and refusal behavior are unchanged.
- [ ] The branch contains no Catalyst deployment changes.
- [ ] Each V2.1 issue has an owner, dependency and acceptance test.

**Deliverable:** `docs/v2.1/phase-m0-baseline.md`.

---

# V2.1 Phase M1 — language, voice and multilingual retrieval

## Objective

Move Kannada from the honest offline glossary to approved full-fidelity
translation/ASR/TTS and improve multilingual retrieval without weakening
evidence or access control.

## M1-01 — Bhashini configuration and fallback

1. Obtain approved Bhashini credentials and pipeline/model IDs.
2. Add provider readiness and bounded timeout/retry at the existing
   `LanguageService` boundary.
3. Keep `LocalLexiconLanguageService` as the deterministic fallback.
4. Expose `language_full_fidelity=false` whenever the fallback is active.
5. Store credentials through V3 Catalyst secrets; do not commit them in V2.1.

## M1-02 — Bilingual corpus

1. Generate deterministic English brief facts from curated records.
2. Translate approved synthetic/redacted text only.
3. Store Kannada as an enrichment keyed to the source case/row, with source
   hash, language, provider, version, timestamp and status.
4. Cache by source hash and make replay idempotent.
5. Make translation failure non-blocking for English curated loading while
   reporting the Kannada deficit.

## M1-03 — Server ASR and TTS

1. Enforce audio MIME, byte, duration, language and per-user limits.
2. Send bytes only to the language service; do not persist raw audio by default.
3. Return transcript, language, provider status, confidence and correlation ID.
4. Generate TTS only after evidence composition and language rendering.
5. Store audio through the V3 FileStore contract with owner-qualified,
   short-lived access.
6. Add UI playback only when an authorized audio URL exists.

## M1-04 — Multilingual embeddings

1. Keep hashed n-gram embeddings as the local test oracle.
2. Add an approved multilingual model behind `EmbeddingModel`.
3. Store model/version/dimension/language/source hash/index time.
4. Run old/new indexes in shadow mode and compare authorized case IDs,
   locators, rank stability and latency.
5. Switch only through a versioned setting after evaluation passes.
6. Preserve ACL prefiltering before ranking or model calls.

## M1 verification

- [ ] English/Kannada/code-mixed routing is correct or safely clarified.
- [ ] Kannada retrieval maps back to the same authorized source locators.
- [ ] ASR failure is explicit and never guessed.
- [ ] TTS is owner-authorized and expires/purges correctly.
- [ ] Translation/embedding replay creates no duplicates.
- [ ] Fallback state is truthful in API and UI.

---

# V2.1 Phase M2 — formal LLM input/output evaluation

## Objective

Prove that an approved LLM provider can improve language flexibility without
changing facts, access control, evidence or safety behavior.

## M2-01 — Level A provider contracts

Test each `LLMGateway` adapter with sanitized recorded responses:

- request and response schema;
- prompt/version metadata;
- timeout/retry/quota behavior;
- malformed output handling;
- token/max-output budget;
- redaction and minimum-data behavior; and
- deterministic fallback.

## M2-02 — Level B deterministic comparison

For every fixture, compare local deterministic and candidate-provider paths for:

- intent and specialist route;
- slots/coreference;
- scope and permissions;
- case/edge/alert/aggregate IDs;
- evidence locators and provenance markers;
- structured payload and computation trace; and
- refusal/clarification behavior.

Wording may change. Facts, numbers, evidence, permissions, safety labels and
inferred/synthetic markers may not.

## M2-03 — Level C live provider acceptance

This is blocked until V3 provides an isolated Catalyst Development project and
an approved provider/key. Then:

1. Run only synthetic/redacted fixtures.
2. Store provider/model/prompt versions, response hash, latency, tokens,
   checks, correlation ID and result in the V3 evaluation storage contract.
3. Require 100% evidence-validator pass, no scope leakage, no prohibited
   individual prediction, marker preservation and safe provider-failure
   fallback.
4. Obtain bilingual reviewer scores for terminology/readability; reviewers do
   not replace the deterministic factual oracle.

## M2-04 — Change control

Version provider, model, prompt, temperature, token budget and language path.
Run the fixed corpus for every change, produce a factual/latency/cost diff,
obtain policy/technical approval and retain a rollback setting.

## M2 verification

- [ ] Level A and B remain offline-reproducible.
- [ ] Level C is either passed with an approved provider or explicitly blocked.
- [ ] No LLM call can retrieve, calculate, authorize or invent facts.
- [ ] Provider outage returns deterministic/fail-safe behavior.
- [ ] Prompt/provider version and rollback are recorded.

---

# V2.1 Phase M3 — aggregate forecasting and analytical completion

## Objective

Add the requested prediction capability as a cautious, evidence-bearing
aggregate planning forecast—not individual future-crime prediction.

## M3-01 — Forecast contract and model card

Define the forecast unit as:

```text
approved place/unit × time bucket × crime category
```

Each output must contain expected count/range, uncertainty, training window,
data freshness, baseline comparison, model/version, sample size and caveat.
The model card must define prohibited uses and fallback behavior.

## M3-02 — Deterministic baseline

1. Build time/place/crime-type aggregate counts.
2. Implement seasonal-naive and rolling-rate baselines first.
3. Add Poisson/negative-binomial or another reproducible count model only when
   diagnostics justify it.
4. Store versioned output and evidence in a `cip_aggregate_forecast` contract.
5. Add `FORECAST_QUERY` routing to `CrimeAnalyticsAgent`.
6. Distinguish observed, early-warning and forecast visualizations in the UI.

## M3-03 — Temporal backtesting

Use rolling-origin holdouts. Compare baseline/candidate using appropriate count
loss, interval coverage/width, calibration and alert precision/recall by unit
and crime type. Reject a candidate that is materially worse than baseline or
has unacceptable interval coverage.

## M3-04 — Publication and caveats

Forecast publication must be separate from observed intelligence. A failed
forecast cannot remove the last valid observed result. API, UI, trace and
LLM-polished text must retain uncertainty and non-certainty language.

## M3 verification

- [ ] No endpoint or prompt produces a named-person future-crime prediction.
- [ ] Forecasts are aggregate, versioned, uncertain and evidence-bound.
- [ ] Temporal backtest beats or meets the approved baseline.
- [ ] Sparse cells are suppressed/marked unstable.
- [ ] Forecast failure/rollback preserves last valid output.
- [ ] Evaluation fixtures cover prohibited prediction requests.

---

# V2.1 Phase M4 — governed socio-economic and financial intelligence

## Objective

Complete data integrations only when their ownership, data quality, privacy and
retention contracts are approved.

## M4-01 — Socio-economic reference data

Required contract: owner, license, geography/time keys, revision policy, update
cadence, fields, retention, roles and aggregate-only linkage rules.

Implementation:

1. Land versioned extracts through the V3 Stratus contract.
2. Register source/checksum/retrieval metadata.
3. Validate schema, geography/time coverage, units, missingness, duplicates,
   outliers and join coverage.
4. Store normalized indicators in an `ext_` reference table.
5. Join only at approved aggregate levels.
6. Apply small-cell suppression and denominator labels.
7. State correlation/limitations; never claim causation.

If approval or a reliable dataset is absent, keep the feature blocked and use
the current limited aggregate sociology behavior.

## M4-02 — Real financial ingestion

Until written approval exists, retain the synthetic financial extension and its
markers. If approved:

1. Define source authority, fields, tokenization, retention, legal basis,
   reconciliation and permitted roles.
2. Land controlled extracts through the V3 Stratus contract.
3. Validate checksum, schema, currency/time, duplicates, reversals and totals.
4. Tokenize identifiers before graph enrichment.
5. Load versioned extension rows with provenance/sensitivity/run ID.
6. Reconcile source/control totals before publication.
7. Enforce `FINANCIAL_VIEW` and preserve real-versus-synthetic markers.
8. Test purge and incident response.

## M4 verification

- [ ] No unapproved external source is connected.
- [ ] Every indicator/transaction has provenance and version evidence.
- [ ] Sensitive data is aggregate-only or explicitly authorized.
- [ ] Financial totals reconcile and replay is idempotent.
- [ ] Unauthorized users cannot receive financial details.
- [ ] Causal or individual-risk claims are refused.

---

# V2.1 Phase M5 — entity resolution, retrieval and investigation quality

## Objective

Improve quality and scale while preserving reversible identity links and
source-record integrity.

## M5-01 — Entity-resolution calibration

1. Expand labelled synthetic pairs for transliteration, initials, honorifics,
   age drift, missing fields and near misses.
2. Add approved analyst-labelled samples when permitted.
3. Measure precision, recall, false-link and review rates by district/language.
4. Change auto/review/reject thresholds only through a versioned report.
5. Persist reviewer actor/time/reason; never merge source rows destructively.
6. Trigger graph/score refresh after an approved decision.
7. Prove rejected links do not return after cache/pipeline replay.

## M5-02 — Retrieval quality

1. Compare embedding versions with ACL-first retrieval.
2. Measure authorized recall@k, locator precision, rank stability and latency.
3. Ensure every result maps to a source case/row/offset.
4. Add multilingual/code-mixed/typo fixtures.
5. Keep deterministic fallback when a model is unavailable.

## M5-03 — Investigation support quality

Test case summaries, timelines, similar cases, graph leads and case-priority
indicators for:

- evidence completeness;
- correct scope;
- provenance markers;
- no accusation language for inferred links;
- clear “recorded history/case priority, not future prediction” labels; and
- stable output under repeat runs.

## M5-04 — Minor application fixes

Track and resolve these small issues without deployment rewrites:

- register pytest markers to remove unknown-marker warnings;
- replace deprecated FastAPI startup hooks with lifespan when compatible;
- verify current Starlette/httpx test-client compatibility;
- add Catalyst table/column allow-lists at adapter boundaries;
- document non-atomic Catalyst upserts and prevent their use for counters;
- improve short-history early-warning/seasonality messaging;
- bound NetworkX graph expansion and report withheld links;
- add cache invalidation tests after intelligence refresh; and
- keep UI labels consistent for observed, inferred, synthetic and forecast
  outputs.

## M5 verification

- [ ] ER metrics and reviewer decisions are durable and reproducible.
- [ ] Retrieval never returns out-of-scope records before ranking.
- [ ] Investigation outputs preserve evidence and safety labels.
- [ ] Minor warnings/fixes have regression tests.
- [ ] Performance limits are measured on the approved synthetic scale.

---

# V2.1 Phase M6 — handoff to the Catalyst deployment branch

## Objective

Package approved V2.1 feature changes as contracts that V3 can deploy without
mixing feature and infrastructure work.

## Steps

1. Produce a V2.1 release manifest with application/API schema changes,
   feature flags, model/prompt/embedding versions and required Catalyst
   resources.
2. Run the complete local suite and evaluation corpus.
3. Identify each required Catalyst table, Stratus prefix, Circuit stage,
   secret/configuration name and permission.
4. Open a V3 integration change containing only the deployment wiring/resource
   provisioning; do not copy broad branch changes manually.
5. Run V3 parity and deployed E2E tests with the feature disabled and enabled.
6. Retain rollback settings for every new feature/model/provider.

## V2.1 exit gate

- [ ] Every pending V2 feature is complete, approved, or explicitly blocked.
- [ ] Forecasting is aggregate-only, backtested and uncertainty-labelled.
- [ ] Language/voice and LLM behavior passes its evaluation level.
- [ ] External socio-economic/financial work has a signed data contract or
      remains disabled and clearly labelled.
- [ ] Retrieval/ER/investigation quality has measured acceptance criteria.
- [ ] No individual future-crime prediction exists.
- [ ] Local regression suite and evaluation corpus pass.
- [ ] V3 integration requirements are documented and branch-isolated.

## 5. V2.1 pending/blocked matrix

| V2 item | State | V2.1 action | Dependency/owner |
|---|---|---|---|
| Live Data Store/Stratus parity | Deployment-owned | Do not duplicate; provide contracts/tests | V3 |
| Active publication/last-good rollback | Deployment-owned | Define feature publication requirements | V3 |
| Catalyst Auth/NoSQL/Cache live use | Deployment-owned | Preserve ports; add integration expectations | V3 |
| Bhashini translation/ASR/TTS | Blocked/pending | Implement through `LanguageService`, test fallback | Credentials/approval + V3 secrets |
| Bilingual corpus/embeddings | Pending | Add enrichment/provenance and shadow evaluation | Bhashini/model approval |
| Circuit dashboards/alerts | Deployment-owned | Define feature metrics and acceptance | V3 |
| Aggregate forecasting | Pending | Implement model card, baseline, backtest, API/UI | Data history/model approval |
| External socio-economic data | Blocked | Prepare contract/DQ/reference layer only | Data owner/governance |
| Real financial ingestion | Blocked | Keep synthetic; implement only after legal approval | Financial owner/governance |
| Level C LLM evaluation | Blocked | Run when V3 provides isolated Catalyst/provider | Provider approval/credentials |
| ER/retrieval quality | Hardening | Expand labelled calibration/shadow tests | Approved labelled data |
| Security/pilot readiness | Deployment/governance-owned | Provide feature-specific controls and tests | V3/security/policy |
| Individual future-crime prediction | Deferred/prohibited | Do not implement | Policy/safety boundary |

## 6. V2.1 branch workflow

```powershell
git switch implementation-v2.1-maintenance
git pull --ff-only origin implementation-v2.1-maintenance
# make only application-feature/quality changes
git add backend frontend docs implementationv2.1.md
git commit -m "Complete V2.1 intelligence features"
git push -u origin implementation-v2.1-maintenance
```

Before pushing:

- inspect `git diff --cached`;
- verify no Catalyst descriptor, secret, local database, generated landing
  file, export or deployment artifact is staged accidentally;
- run the local suite and the relevant evaluation corpus; and
- document any V3 integration commit required.

## 7. Accurate V2.1 completion statement

V2.1 is complete only when the remaining product capabilities are implemented,
tested and approved independently of deployment. The correct statement after
completion is:

> KSP-CIP V2.1 completed the approved language, voice, aggregate forecasting,
> governed data-integration, retrieval-quality, ER-calibration and LLM-
> evaluation work. Catalyst deployment status remains determined only by the
> V3 branch and its live deployment gates.
