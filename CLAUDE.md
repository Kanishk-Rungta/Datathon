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
    tests/             unit/ (7 files) + integration/ (4 files)
  frontend/            React + Vite console
  catalyst/            catalyst.json, functions/, appsail/, circuits/
  docs/adr/            ADR-0001 … ADR-0006
  scripts/             setup.sh, seed.sh, run.sh, dev.sh, test.sh
```

---

## 4. Build, run, test

```bash
scripts/setup.sh                 # deps + console build
scripts/seed.sh 4200 30          # synthetic data + all derived intelligence
scripts/run.sh                   # http://127.0.0.1:8000  (API + console)
scripts/test.sh                  # full suite
scripts/test.sh -m "not slow"    # unit tests only (no seeding)
scripts/dev.sh                   # hot reload, API :8000 + Vite :5173
```

CLI equivalents: `python -m ksp_cip.cli {seed,refresh,check,dq,serve,config}`.

`cli check` is the fastest way to see what state a checkout is in — it reports
row counts, DQ results, graph size, ER buckets and which providers are active.

Demo accounts are listed on the login screen. Password: `ChangeMe#2026`.
Roles: `io.bengaluru` (station scope), `analyst.state`, `sp.mysuru`,
`policy.home` (aggregates only), `auditor.internal`, `admin.platform`.

---

## 5. Status

**All 30 Definition-of-Done items are implemented.** 201 tests pass.

| Area | State |
|---|---|
| Domain, config, ports | complete |
| SQLite adapter, migrations, 26 curated + control + intelligence tables | complete |
| Repositories (14) | complete |
| Deterministic services (7) | complete |
| Analytics engine + pure stats | complete |
| Entity resolution, graph builder, NetworkX queries, financial | complete |
| RAG (ACL-prefiltered hybrid retrieval) | complete |
| NLU (13 intents, deterministic) | complete |
| Five agents + supervisor | complete |
| Pipeline (generate → land → load → DQ → intelligence) | complete |
| FastAPI, 36 endpoints, RFC 9457, correlation IDs | complete |
| React console (chat, evidence, charts, graph, review queue) | complete |
| Catalyst adapter + deployment shell | complete, **not run against live Catalyst** |
| Tests: 201 (unit + integration) | passing |
| ADRs 0001–0006 | complete |

### Known limitations (stated, not hidden)

- **Kannada is a glossary, not a translator**, unless Bhashini is configured.
  The platform reports `language_full_fidelity: false` and the console shows
  "kannada: offline glossary" in the status bar.
- **Catalyst deployment is untested against a live project.** Translation and
  escaping are covered by contract tests; the network path is not.
- **NetworkX is in-memory** — fine at hackathon scale, not statewide.
- **Grid binning is coarser than kernel density**; a cell boundary can split a
  real concentration. Stated in the answer and the UI.
- **Voice** uses the browser Web Speech API locally; server-side ASR requires
  Bhashini.
- `sample_stddev` is used for the early-warning baseline; with very short
  histories the z-score floor (1.0) does most of the work.

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
- **Hotspot cases are planted inside the 90-day detection window** on purpose.
  Spreading them over the full period makes the validation loop meaningless.
- `AnalyticsEngine.investigation_priority` takes keyword-only primitives;
  callers with a `CaseSummary` should use `priority_for_summary`.

---

## 7. The validation loop

The generator plants signals and records them in a manifest
(`manifests/seed_manifest.json`, also returned by `cli seed`):

- a **surge** in one district × crime type,
- three geographic **hotspots**,
- a **network ring** whose members appear under transliteration variants,
- a long tail of **repeat offenders**.

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

1. `scripts/setup.sh && scripts/seed.sh 1500 24 && scripts/run.sh`
2. Sign in as `analyst.state`, ask "Where are the hotspots?", then click a
   citation chip — that round trip exercises most of the system.
3. Sign in as `io.bengaluru` and ask the same thing to see scope enforcement.
4. Read ADR-0002 and ADR-0003 before changing anything in `application/agents`
   or `application/services/evidence.py`.
5. `scripts/test.sh -m "not slow"` runs in seconds and covers the rules above.
