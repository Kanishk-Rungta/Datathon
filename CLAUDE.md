# CLAUDE.md — engineering memory

This file exists so a future session can resume from the repository alone.
It is kept current with the code; if something here disagrees with the code,
the code is right and this file is a bug.

---

## 1. What this is

**KSP-CIP** — the Karnataka State Police Crime Intelligence Platform. A
conversational intelligence system over FIR records: an officer asks a question
in English or Kannada and receives an answer in which every factual statement
is bound to the source records it rests on.

Built from two frozen specifications (`KSP-CIP-Architecture-v1_0_1.md` and
`KSP-CIP-Hackathon-Implementation-Plan.md`) plus the organiser's ER document
(`Police_FIR_ER_Diagram.pdf`). **The organiser's schema is unchanged.**

Everything runs locally with **zero credentials**. All data is synthetic.

---

## 2. The five rules that shape every design choice

Break any of these and the platform stops being safe for police use. They are
enforced by code and tests, not by convention.

1. **The LLM never knows anything.** It phrases and routes. Every figure comes
   from arithmetic in `application/analytics`. Enforced by
   `AnswerComposer._enforce_evidence` and `verify_rewrite`. See ADR-0003.
2. **No claim without evidence.** A numeric or inferred claim without a
   verifiable locator raises `EvidenceMissingError` and the answer never
   ships. Negative answers are evidenced too (`empty_result_evidence`).
3. **Inference is labelled.** Derived links carry `Provenance.INFERRED` and
   render as `(inferred)`. Synthetic financial data renders as
   `(synthetic extension)`. Markers survive translation, LLM polish and PDF.
4. **Scope is in the query, not the filter.** Authorization is injected into
   the SQL `WHERE` clause and into graph traversal. A record outside a
   caller's unit subtree is never retrieved, so it cannot leak through a
   snippet.
5. **Identity is provisional.** Entity resolution auto-links at τ≥0.90, queues
   0.72–0.90 for human review, and **never merges** — identities are connected
   components over auto-links and every source row id is retained.

---

## 3. Layout

```
ksp-cip/
  backend/
    ksp_cip/
      config/          Settings (env prefix KSPCIP_)
      domain/          errors, enums, value_objects, models, ports  [no I/O]
      application/
        agents/        the FIVE agents + supervisor
        services/      deterministic: authorization, audit, evidence, memory,
                       language, identity, pdf_export
        analytics/     stats.py (pure) + engine.py (trend/hotspot/EW/socio/IPI)
        graph/         entity_resolution, builder, service (NetworkX), financial
        rag/           retrieval (ACL pre-filter, hybrid ranking)
        nlu/           rule classifier + slot extraction
        pipeline/      generators/, loader, dq, intelligence, orchestrator
      infrastructure/
        db/            schema.sql, sqlite_store, migrations, kv_store, repositories/
        catalyst/      datastore (ZCQL), stratus
        llm/           gateway + providers (local deterministic default)
        language/      Bhashini + offline Kannada lexicon
        embeddings/    hashed n-gram TF-IDF
        filestore/, observability/
      interface/
        container.py   composition root — everything is wired here, by hand
        api/           main.py, deps.py, schemas.py, routers/ (10)
      resources/       prompts/ (versioned), lexicon/kn_en.json
      cli.py           seed | refresh | check | dq | serve | config
    tests/             unit/ (28 files) + integration/ (10 files) + evals/
  frontend/            React + Vite console
  catalyst/            catalyst.json, functions/, appsail/, circuits/, _bootstrap.py
  docs/adr/            ADR-0001 … ADR-0006
  scripts/             thin wrappers over cip.py (.sh and .ps1)
                       build_catalyst_artifact.py, generate_schema_manifest.py
cip.py                 THE entry point: install, seed, run, test, package
cip.bat                double-clickable Windows wrapper around cip.py
```

`cip.py` at the repository root is the entry point, and the only place the
install/seed/run logic lives. `cip.bat` is a double-clickable Windows wrapper
around it. The `scripts/*.sh` and `scripts/*.ps1` files are thin wrappers that
translate their arguments and hand over — they used to be two more copies of
"make a venv, install these packages, build the console", which is two more
chances to install a different set.

Nothing in `cip.py` imports `ksp_cip`: it is stdlib-only because its first job
is to build the virtualenv the application needs, and it launches the app as a
subprocess so `get_app()` stays the only application factory. Pinned by
`tests/unit/test_launcher.py`.

---

## 4. Build, run, test

```bash
python cip.py                    # install if needed, seed if needed, serve
python cip.py doctor             # what state is this checkout in, and what next
python cip.py setup              # deps + console build only
python cip.py seed --cases 4200 --months 30 [--reset]
python cip.py run --port 8000 [--reload]
python cip.py test [-m "not slow"]
python cip.py dev                # hot reload, API :8000 + Vite :5173
python cip.py package            # stage the three Catalyst artifacts into dist/
```

On Windows, `cip.bat` is the same thing and can be double-clicked. The
`scripts/*.sh` and `scripts/*.ps1` wrappers still work and forward to these.

The wrappers, which some docs still reference:

```powershell
.\scripts\setup.ps1
.\scripts\seed.ps1 -Cases 4200 -Months 30 [-Reset]
.\scripts\run.ps1 [-Port 8000]
.\scripts\test.ps1 [-m "not slow"]
.\scripts\dev.ps1
```

CLI equivalents: `python -m ksp_cip.cli {seed,refresh,check,dq,serve,config}`.

`cli check` is the fastest way to see what state a checkout is in — it reports
row counts, DQ results, graph size, ER buckets and which providers are active.

Demo accounts are listed on the login screen. Password: `ChangeMe#2026`.
Roles: `io.bengaluru` (station scope), `analyst.state`, `sp.mysuru`,
`policy.home` (aggregates only), `auditor.internal`, `admin.platform`.

---

## 5. Status

**All 30 Definition-of-Done items are implemented.** **593 passing, 9
skipped** — the skips are live-Catalyst tests (credentials + explicit opt-in)
and the GPU speech tests. Last full run: 29 Aug 2026, Python 3.11.9 on
Windows, against FastAPI 0.141 / Starlette 1.6 / pydantic 2.13 / numpy 2.4.

Counts have drifted between documents before. The number above is what
`python cip.py test` actually printed; if you change the suite, re-run it and
update this line rather than estimating.

V2 progress against `implementationv2.md` is recorded in
[docs/v2-progress.md](docs/v2-progress.md). Deployment-phase progress against
`implementationv2-phases-0-2.md` (Phases 0–2: baseline, Catalyst runtime,
Catalyst data plane) is recorded in `docs/deployment/phase0-baseline.md`,
`phase1-catalyst-runtime.md`, and `phase2-data-plane.md` — read the relevant
one before starting further deployment work; each states plainly what's
verified versus what needs a live Catalyst project.

| Area | State |
|---|---|
| Domain, config, ports | complete |
| SQLite adapter, migrations (4), 27 curated + control + intelligence + ext tables | complete |
| Repositories (16) | complete (includes `SocioEconomicRepository`) |
| Deterministic services (7) | complete |
| Analytics engine + pure stats | complete (trend, hotspot, early warning, seasonality, event comparison, sociology, socio-economic correlation, IPI) |
| AI Spatio-Temporal Predictive Forecasting | complete (`SocioEconomicCorrelator` + `SocioTemporalForecaster` spatial Poisson intensity model + `POST /analytics/spatiotemporal-forecast`) |
| Entity resolution, graph builder, NetworkX queries | complete, calibrated |
| Scalable Graph Database Adapter (Neo4j) | complete (`Neo4jGraphAdapter` — Cypher-native expand/shortest-path/centrality/sync; automatic fallback to NetworkX; zero-code backend switch via `KSPCIP_GRAPH_BACKEND=neo4j`) |
| Socio-economic data ingestion & correlation (`ext_socioeconomic_indicator`) | complete (`SocioEconomicCorrelator` + NLU + agent + endpoint + inspector UI) |
| Financial extension analysis (chains, concentration, bursts, network position) | complete; synthetic data only — real ingestion remains governance-blocked |
| RAG (ACL-prefiltered hybrid retrieval) | complete |
| NLU (15 intents, deterministic) | complete |
| Five agents + supervisor | complete |
| Pipeline (generate → land → load → DQ → intelligence → socio-economic) | complete |
| FastAPI, 43 operations over 42 paths, RFC 9457, correlation IDs | complete |
| React console (chat, evidence, charts, graph, review queue, socio-economic view) | complete |
| Catalyst adapters (Data Store, Stratus, NoSQL, Cache, Identity) | complete, **not run against live Catalyst** |
| Catalyst runtime (AppSail api + console services, event refresh function, packaging for all three) | complete, **not deployed to a live project** |
| Schema-capability port + provisioning manifest | complete, calibrated against live SQLite |
| Agent evaluation corpus + harness | complete (Levels A/B; Level C needs a live provider) |
| Voice (AI4Bharat self-hosted ASR/TTS, `speech-service/`) | adapter + GPU launcher + WebSocket stream + wiring complete and tested |
| Tests: 602 (593 passing + 9 skipped live-Catalyst/GPU) | passing |
| ADRs 0001–0006 | complete |

### Known limitations (stated, not hidden)

- **Kannada is a glossary, not a translator** unless Bhashini *or* the
  self-hosted AI4Bharat service is configured. The platform reports
  `language_full_fidelity: false` and the console shows "kannada: offline
  glossary" in the status bar. See `docs/voice-ai4bharat.md` for the
  self-hosted path (`speech-service/`), which needs no credentials — only
  hardware. **Its model-loading code has not been run against real weights**
  (no GPU available); the HTTP contract on both sides is tested, the model
  calls are not, and there is no domain-vocabulary benchmark yet.
- **Catalyst deployment is untested against a live project.** Translation and
  escaping are covered by contract tests; the network path is not.
- **NetworkX is in-memory** — fine at hackathon scale, not statewide.
- **Grid binning is coarser than kernel density**; a cell boundary can split a
  real concentration. Stated in the answer and the UI.
- **Voice** uses the browser Web Speech API by default. Server-side ASR/TTS
  needs either Bhashini or the self-hosted AI4Bharat service; the console
  requests spoken answers only when `language_full_fidelity` is true. Batch,
  not streaming.
- `sample_stddev` is used for the early-warning baseline; with very short
  histories the z-score floor (1.0) does most of the work.
- **Seasonality needs two prior years per calendar month.** Below that a bucket
  is marked `insufficient_history` and reports no deviation, so a 24-month seed
  yields few reportable months. That is intended, not a bug.
- **The ZCQL upsert emulation is not atomic.** `INSERT … ON CONFLICT` is
  translated to read-then-write; concurrent writers can duplicate. Every caller
  is a replayable pipeline stage on a deterministic key. Do not use it for a
  counter.
- **Event comparison shows coincidence, never cause.** The result type has no
  causal field and a test asserts it stays that way.

---

## 6. Things that will bite you

- **`Slots` is `extra="forbid"`.** Adding a slot means editing
  `domain/models.py` as well as the classifier.
- **The composer rejects unevidenced numbers, including in "nothing found"
  answers.** If a claim contains a digit — even "12-month baseline" — attach
  `empty_result_evidence`. Do not weaken the rule.
- **Intent weights matter.** `INVESTIGATION_SUMMARY` is deliberately 0.90,
  below the specific intents, so "summarise the demographics" is not read as a
  case briefing.
- **Crime keywords expand to families.** "theft" resolves to House/Motor
  Vehicle/Other Theft. Matching a single sub-head silently narrows the
  question.
- **Unresolved place names are refused, not dropped.** `slots.unresolved_terms`
  drives an honest refusal in `DataRetrievalAgent._case_search`.
- **The loader conforms to the real table columns** (`PRAGMA table_info`).
  A generator column the schema does not declare is dropped and logged — this
  is what keeps the organiser's schema authoritative.
- **`/files/{path}` enforces ownership**, not just authentication. Export keys
  are `exports/<user_id>/...`. See `authorize_file_access`.
- **A resolved identity has many source rows; financial totals must use them
  all.** `FinancialAnalyzer.summarize()` takes `subject_refs`, not just
  `subject_ref`. Entity resolution routinely merges several `curated_Accused`
  rows into one person, and a transfer may be recorded against any of them.
  Matching on the display ref alone counted such a transfer in the transaction
  *count* while contributing nothing to the totals — a person with real money
  movement was reported as "1 transaction … ₹0 received and ₹0 sent". Pinned by
  `TestSubjectTotals`.
- **The financial API's console gate is `transaction_count`.** The Inspector
  panel used to test `financial.transactions.length`, a key the endpoint never
  returned, so the panel never rendered at all. Counterparty rows use
  `txn_count`/`ref`; keep the API and `Inspector.jsx` in step.
- **An owned key's *first* segment must be the user id.** Synthesised audio was
  written to `audio/<session_id>/...`, which resolves to no owner, so
  `authorize_file_access` refused it to everyone — including the user who
  requested it. Latent only because no provider returned audio bytes. Keys are
  now `audio/<user_id>/<session>/<sha256>.wav`; use `chat.audio_key()` rather
  than building one by hand, and never key an owned artefact by session.
  `hash()` is salted per interpreter, so it must not appear in a key either.
- **Hotspot cases are planted inside the 90-day detection window** on purpose.
  Spreading them over the full period makes the validation loop meaningless.
- **Backend selectors are independent switches.** `DATASTORE_BACKEND=catalyst`
  with `FILESTORE_BACKEND=local` is refused at startup: exports on a function
  filesystem vanish at the next cold start. See `Settings.deployment_problems`.
- **Conflicting initials demote an ER pair to review**, they do not veto it.
  "K. Prakash Naik" vs "M. Prakash Naik" scored 0.94 and auto-linked before
  this was added. See `tests/unit/test_entity_resolution_calibration.py`.
- `AnalyticsEngine.investigation_priority` takes keyword-only primitives;
  callers with a `CaseSummary` should use `priority_for_summary`.
- **`cip_api` is an AppSail service, not a Function.** The original Advanced
  I/O handler (`def handler(context, basicio): return app`) had no documented
  ASGI bridge and could not have worked. See
  `docs/deployment/catalyst-runtime.md`. `catalyst/appsail/api/server.py`
  runs `uvicorn` directly; `get_app()` is still the only application factory.
- **`catalyst/_bootstrap.py` resolves two different layouts on purpose.** It
  checks for a `ksp_cip` sibling first (a staged artifact from
  `scripts/build_catalyst_artifact.py`), then falls back to the repo-relative
  `backend/ksp_cip` (running an entrypoint straight out of `catalyst/` during
  development). Don't simplify this to one path — that's exactly the defect
  P1-03 exists to catch.
- **`DataStore.table_columns()` replaces raw `PRAGMA table_info(...)`** in
  `loader.py`. Catalyst's implementation parses `schema.sql` + every
  `migrations.py` entry (`infrastructure/db/schema_reflection.py`) — it
  originally missed a migration-added column until a live-SQLite parity test
  caught it. If you add a migration, `test_schema_reflection_parity.py` will
  fail if the new column/table isn't reachable through this path.
- **A seed that stops after `refresh_all()` looks finished and is not.**
  `_seed_users`, `_seed_events` and `_seed_socioeconomic` all run *after* the
  intelligence refresh, and each is a no-op when its table already has rows.
  The checked-in `backend/var/ksp_cip.db` was found in exactly that state:
  4,200 cases, 26k graph edges — and zero rows in `cip_user_account`, so
  nobody could sign in to a fresh clone. Judge a seeded database by the tail
  of the pipeline, not the case count. `cli check` reports `cases`, which is
  not enough; `tests/integration/test_seed_reset.py` asserts the tail.
- **Every child of `curated_CaseMaster` must be in `_truncate()`.** Foreign
  keys are on, so a missing child table makes `seed --reset` die on
  "FOREIGN KEY constraint failed" before it writes anything.
  `curated_ComplainantDetails` was the one that was missing. If you add a
  table that references a curated parent, add it there too.
- **`_seasonal` and `_forecast` both have early-return branches, and the
  governance sentence has to be on all of them.** The "not a forecast"
  disclaimer used to live only on the branch that had something to report, so
  the honest "not enough history" answer shipped without it — and whether that
  branch fires depends on the day of the month, because the seed window is
  anchored to today. Use `SEASONAL_DISCLAIMER`; don't retype the sentence.
- **`pinned_person_names` is not "the current question mentions a person".**
  A pin lasts the whole session. `MemoryService.resolve_coreference` already
  promotes a pinned name into `slots.person_names` when the text contains a
  person anaphor, so a refusal gate should read the *slots*. Reading the pin
  made "forecast crime for the next three months" refuse as an individual
  prediction for the rest of any conversation that had mentioned an offender.
  Pinned names are still the right fallback for agents that are *answering*
  about a person — the distinction is refusing vs resolving.
- **Nothing outside a Catalyst component's own directory is deployed.**
  Catalyst zips only the directory named by `source`. That is why
  `build_catalyst_artifact.py` exists, and why it has a `console` target as
  well as the two Python ones: `server.js` reads the console build from
  `frontend/dist`, which is outside `appsail/console`. `catalyst.json` records
  `source` (where it is maintained) and `deploy_source` (what to ship)
  separately, and a test asserts both are declared.
- **`httpx` is a runtime dependency, not a dev extra.** The hosted LLM
  providers and the Bhashini adapter import it lazily, so leaving it out of
  the deployment requirements meant flipping `KSPCIP_LLM_PROVIDER` started
  cleanly and then raised `ModuleNotFoundError` on the first real call. Every
  other outbound adapter (Catalyst Data Store/Stratus/Cache, AI4Bharat) uses
  stdlib `urllib` deliberately — prefer that for anything new.
- **`command -v python3` finds a Python that does not exist on Windows.** The
  Microsoft Store App Execution Alias is on PATH and prints an advert instead
  of running. `scripts/setup.sh` and `scripts/_python.sh` now test each
  candidate before accepting it; don't simplify that back to the first name
  that resolves.
- **The startup hook is a lifespan handler, not `@app.on_event`.** The event
  decorators are deprecated and scheduled for removal, and the Catalyst Python
  stack version is chosen at provisioning time — so the app must not depend on
  an API a newer pinned FastAPI would drop.
- **`cip.py` must never import `ksp_cip`, and must never build the app.** It
  runs on a bare interpreter *before* the virtualenv exists — that is its whole
  job — and it launches the application as a subprocess so `get_app()` stays
  the only application factory. Catalyst runs `catalyst/appsail/api/server.py`
  and never runs `cip.py`, so anything configured in the launcher and not in
  the entrypoint is a behaviour that works locally and silently differs in
  deployment. `tests/unit/test_launcher.py` fails the build on either.
- **`cip.py`'s package list has to track `pyproject.toml`.** A dependency added
  to one and not the other produces a virtualenv that cannot run the thing it
  was built for. `TestPackageListMatchesTheProject` compares them.
- **Argparse cannot be given pytest's flags.** `cip.py test -m "not slow"`
  binds `-m` to the wrong parser even with `nargs=REMAINDER`, so `main()`
  intercepts `test` before argparse sees it. `scripts/test.ps1` has no
  `param()` block for the same reason. Don't "tidy" either of them.
- **Don't name a Windows launcher `start.bat`.** `start` is a cmd built-in and
  shadows a file of that name typed without its extension. Hence `cip.bat`.

---

## 7. The validation loop

The generator plants signals and records them in a manifest
(`manifests/seed_manifest.json`, also returned by `cli seed`):

- a **surge** in one district × crime type,
- three geographic **hotspots**,
- a **network ring** whose members appear under transliteration variants,
- a long tail of **repeat offenders**,
- a **financial burst**: one account given a spike of transfers on a known day
  against a deliberately quiet run-up. Without it the burst analysis has
  nothing to find — the ordinary generator never produces more than two
  transfers per account per day, so the code path would be untested.

`tests/integration/test_pipeline_and_data.py::TestPlantedSignalsAreDetected`
asserts the analytics recover each one. Typical run: the Ballari / Online
Financial Fraud surge is detected at z ≈ 4.8, and planted hotspot districts
appear among the top cells.

If you change the generator, keep the manifest and these tests in step —
they are the only reason analytics over synthetic data mean anything.

---

## 8. Configuration

All settings use the `KSPCIP_` prefix; see `backend/.env.example`. The
defaults are chosen so nothing must be set.

| Variable | Default | Note |
|---|---|---|
| `KSPCIP_DATASTORE_BACKEND` | `sqlite` | `catalyst` switches adapters |
| `KSPCIP_LLM_PROVIDER` | `local` | deterministic, offline, no key |
| `KSPCIP_LANGUAGE_PROVIDER` | `local` | offline Kannada glossary |
| `KSPCIP_JWT_SECRET` | dev placeholder | **change before any deployment** |
| `KSPCIP_SYNTHETIC_SEED` | `20260725` | same seed → identical dataset |
| `KSPCIP_ENTITY_RESOLUTION_TAU_HIGH/LOW` | `0.90` / `0.72` | published in the UI |

---

## 9. If you are picking this up cold

1. `python cip.py` — installs, seeds if needed, and serves. Nothing else is
   required, and `python cip.py doctor` explains anything that goes wrong.
2. Sign in as `analyst.state`, ask "Where are the hotspots?", then click a
   citation chip — that round trip exercises most of the system.
3. Sign in as `io.bengaluru` and ask the same thing to see scope enforcement.
4. Read ADR-0002 and ADR-0003 before changing anything in `application/agents`
   or `application/services/evidence.py`.
5. `python cip.py test -m "not slow"` runs in seconds and covers the rules
   above.

A 401 on every account means the database is half-seeded — see the
`_seed_users` note in §6. `cip.py` now detects and repairs that on startup, so
you should only ever see it if you seeded by another route.

---

## 10. Changes in the 29 Aug 2026 hardening pass

Recorded here because several of them are invisible until you deploy, and one
of them was blocking a fresh clone outright.

| Defect | Fix |
|---|---|
| Checked-in database had 4,200 cases and **zero user accounts** — a fresh clone could not sign in | Re-seeded to completion; `test_seed_reset.py` now asserts the tail of the pipeline |
| `seed --reset` always failed on a foreign key | `curated_ComplainantDetails` (and `cip_event_calendar`) added to `_truncate()` |
| Seasonal answers with too little history shipped without the "not a forecast" disclaimer | `SEASONAL_DISCLAIMER` attached on every branch; `test_seasonality_disclaimer.py` |
| An aggregate forecast was refused as an individual prediction for the rest of any session that had mentioned a person | The refusal gate reads `slots.person_names`, not the session pin; `TestForecastScope` |
| `cip-console` would have deployed and served nothing — `server.js` read `frontend/dist`, outside the shipped directory | `resolveDist()` + a `console` target in `build_catalyst_artifact.py`; `deploy_source`/`build` in `catalyst.json`, asserted by a test |
| A first Catalyst deploy always failed on `filestore=local` with `datastore=catalyst` | `_bootstrap.py` defaults both together; `test_catalyst_bootstrap.py` |
| Selecting a hosted LLM or Bhashini raised `ModuleNotFoundError` in deployment | `httpx` promoted to a runtime dependency and added to the AppSail requirements |
| `/capabilities` reported `networkx-in-memory` even on a Neo4j deployment | Read from configuration |
| `scripts/setup.sh` picked the Microsoft Store Python stub on Windows | Candidates are executed before being accepted |
| Three front doors, none of them runnable by double-click on Windows, and each with its own copy of the install logic | `cip.py` (+ `cip.bat`) is the single entry point; the `.sh` and `.ps1` scripts became thin wrappers over it |
| Nothing detected a half-seeded database before it produced a 401 | `cip.py` checks user accounts and socio-economic rows, not just the case count, and re-seeds; `cip.py doctor` reports it |
| `@app.on_event("startup")` (deprecated, slated for removal) | Lifespan handler |
| Static file serving accepted a sibling directory whose name merely began with the document root | Separator-aware containment check |
| The artifact builder imported the FastAPI app while checking the *refresh* function, proving a dependency its requirements do not declare | Per-target import lists |
