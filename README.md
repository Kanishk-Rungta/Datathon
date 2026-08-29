# KSP-CIP — Karnataka State Police Crime Intelligence Platform

**Conversational crime intelligence over the FIR schema, where every factual
statement is bound to the records it rests on.**

An officer asks a question in English or Kannada. The platform answers with a
citation chip on each sentence, the chart or link diagram the answer implies,
and a "how this was computed" panel showing the query, the parameters and the
formula. What a caller may see is decided before the query runs, not after.

> **All data in this build is synthetic.** Nothing here is a real case, a real
> person, or a real financial record. Outputs are intelligence products for
> investigative use — they are not evidence and do not replace the case diary.

| | |
|---|---|
| **Status** | Feature-complete; runs locally with zero credentials |
| **Tests** | **593 passing, 9 skipped** (skips need live Catalyst credentials or a GPU) |
| **Backend** | Python 3.11+, FastAPI, SQLite → Catalyst Data Store, ~21k lines |
| **Frontend** | React 18 + Vite console, ~2.7k lines |
| **Deployment target** | Zoho Catalyst — AppSail × 2, Event Function × 1, Circuit + Cron |
| **Verified against a live Catalyst project** | **No.** See [§10](#10-what-is-not-verified) |

---

## Contents

1. [Quick start](#1-quick-start)
2. [What it does](#2-what-it-does)
3. [What makes it trustworthy](#3-what-makes-it-trustworthy)
4. [Architecture](#4-architecture)
5. [Data model and provenance](#5-data-model-and-provenance)
6. [The analytics, and their formulas](#6-the-analytics-and-their-formulas)
7. [Security and governance](#7-security-and-governance)
8. [How it is verified](#8-how-it-is-verified)
9. [Deploying to Zoho Catalyst](#9-deploying-to-zoho-catalyst)
10. [What is not verified](#10-what-is-not-verified)
11. [Honest limitations](#11-honest-limitations)
12. [Repository map](#12-repository-map)
13. [Documentation index](#13-documentation-index)

---

## 1. Quick start

**Requirements:** Python 3.11+ and (optionally) Node 18+ for the web console.
No API keys, no network, no GPU. The platform ships with a deterministic
offline LLM provider and an offline Kannada glossary, so it runs — and its
tests pass — with zero credentials.

### One command

```bash
python cip.py
```

On Windows you can also just **double-click `cip.bat`**.

That is the whole thing. `cip.py` creates the virtualenv, installs the
dependencies, builds the console, seeds the database if it needs seeding, and
starts the server — skipping whatever is already done, so re-running it is
fast. Then open **http://127.0.0.1:8000** and sign in.

If Python is not on your PATH yet, install it from
[python.org](https://www.python.org/downloads/) and tick **"Add python.exe to
PATH"**. Do not use `python3` on Windows — on a stock install that name is a
Microsoft Store shortcut, not an interpreter.

### The other commands

| Command | What it does |
|---|---|
| `python cip.py` | install what is missing, seed if needed, serve |
| `python cip.py doctor` | report what state this checkout is in, and what to run next |
| `python cip.py setup` | install and build only — no seed, no server |
| `python cip.py seed --cases 1500 --months 24 [--reset]` | build a different dataset |
| `python cip.py run --port 8080 --reload` | serve with options |
| `python cip.py dev` | hot reload: API on :8000, Vite console on :5173 |
| `python cip.py test [-m "not slow"]` | run the test suite; extra arguments go to pytest |
| `python cip.py package` | build the three Catalyst deployment artifacts ([§9](#9-deploying-to-zoho-catalyst)) |

**Start with `python cip.py doctor` if anything looks wrong.** It reports the
Python version, the virtualenv, the dependencies, whether Node and the console
build are present, the row counts in the database, and whether the
configuration is deployable — then names the single command that fixes the
first problem it found.

<details>
<summary>The shell scripts, and the lower-level CLI</summary>

`scripts/*.sh` and `scripts/*.ps1` still exist and still work. They are now
thin wrappers that hand over to `cip.py`, so there is one implementation rather
than three that can drift apart:

| Task | Bash | PowerShell |
|---|---|---|
| Install | `./scripts/setup.sh` | `.\scripts\setup.ps1` |
| Seed | `./scripts/seed.sh 1500 24 --reset` | `.\scripts\seed.ps1 -Cases 1500 -Months 24 -Reset` |
| Serve | `./scripts/run.sh` | `.\scripts\run.ps1 -Port 8000` |
| Test | `./scripts/test.sh -m "not slow"` | `.\scripts\test.ps1 -m "not slow"` |
| Hot reload | `./scripts/dev.sh` | `.\scripts\dev.ps1` |

Below `cip.py` sits the application's own CLI, run from `backend/` inside the
virtualenv. Use it when you want a single pipeline stage rather than the whole
workflow:

```
python -m ksp_cip.cli {seed,refresh,check,dq,serve,config}
```

`cli check` reports row counts, data-quality results, graph size,
entity-resolution buckets and which providers are active. `cli config` prints
the effective configuration with secrets redacted, and exits non-zero if the
configuration is not deployable.

</details>

### What `cip.py` deliberately does not do

It never constructs the application. `ksp_cip.interface.api.main:get_app` stays
the only application factory, and Catalyst runs
`catalyst/appsail/api/server.py`, never this file — so nothing you verify
locally can diverge from what ships. `cip.py` only *prepares*, *launches* and
*packages* the same things. A test asserts it never imports `ksp_cip`.

### Demo accounts

Password for all of them: `ChangeMe#2026`. They are listed on the login screen,
and only in `local` / `development` environments — the endpoint withholds them
in `staging` and `production`, where the data may not be synthetic.

| Account | Role | Sees |
|---|---|---|
| `io.bengaluru` | Investigator | one police station's subtree |
| `analyst.state` | Analyst | statewide, no financial tools |
| `sp.mysuru` | Supervisor | district, plus financial tools and the identity review queue |
| `policy.home` | Policymaker | aggregates only — never a named individual |
| `auditor.internal` | Auditor | the audit trail |
| `admin.platform` | Platform admin | pipeline and data quality |

**Ask the same question as two different roles.** The answers differ, and the
platform says why. Asking `policy.home` "who are the repeat offenders?"
returns a 403 naming the missing permission, not a quietly redacted list.

---

## 2. What it does

**Ask** — "Where are the hotspots?", "What's the trend in Mysuru this year?",
"Who are the repeat offenders?", "How is X connected to Y?", "Brief me on FIR
104430006202600001", "ಮೈಸೂರು ಜಿಲ್ಲೆಯಲ್ಲಿ ಕಳ್ಳತನ ಪ್ರಕರಣಗಳು".

**Get back** — an answer where each sentence carries a clickable citation, a
chart or link diagram, and the computation trace behind it.

Seventeen intents are classified deterministically and routed to one of five
agents. Mapped to the challenge areas:

| # | Capability | How it is answered |
|---|---|---|
| 1 | FIR / case retrieval | SQL with scope in the `WHERE` clause; semantic similar-case search over hashed n-gram TF-IDF embeddings |
| 2 | Criminal networks | NetworkX graph over co-accused / same-location / same-MO / money-flow edges, with entity resolution |
| 3 | Trends and hotspots | Least-squares slope over monthly counts; 750 m grid binning over a 90-day window |
| 4 | Demographics and socio-economics | Occupation / age / gender / religion / caste breakdowns with small-cell suppression; Pearson correlation against district indicators |
| 5 | Repeat-offender profiling | Transparent weighted score over *recorded history*, every weight published |
| 6 | Investigation support | Case briefing, timeline, charges, and an Investigation Priority Indicator |
| 7 | Financial link analysis | Onward-transfer chains, counterparty concentration, per-account bursts, network position (synthetic extension) |
| 8 | Forecasting | Rolling-rate projection with backtest error, plus a spatial Poisson-intensity projection over the grid |
| 9 | Explainable evidence trails | Every claim carries locators; every answer carries a computation trace; PDF export preserves both |
| 10 | RBAC, scope and audit | Six roles, scope injected into SQL and graph traversal, append-only audit trail |

Also: early-warning alerts, seasonality against prior-year baselines,
event-window comparison, PDF export, and a data-quality gate that halts the
pipeline on a blocking failure.

---

## 3. What makes it trustworthy

These are enforced by code and tests, not by prompt wording.

**The language model never knows anything.** It routes when rules are unsure,
and it phrases already-computed answers. Every figure is arithmetic in
`application/analytics`. A polished answer is re-verified: citations must match
exactly, no new number may appear, provenance markers must survive — or the
rewrite is discarded. See [ADR-0003](docs/adr/0003-llm-is-not-a-source-of-truth.md).

**No claim ships without evidence.** A numeric or inferred claim with no
verifiable locator raises `EvidenceMissingError` and the answer never leaves
the composer. Even "nothing found" carries a locator, because that is still an
assertion about a specific query over specific data.

**Inference is visible.** Derived links render as `(inferred)`. Synthetic
financial and socio-economic data render as `(synthetic extension)`, and that
marker is read from the row rather than hard-coded — an approved real
ingestion drops it as a data change, not a code change. In the link diagram,
inferred edges are dashed. Always.

**Authorization is in the query.** A caller's unit subtree is injected into the
SQL `WHERE` clause and into graph traversal. Records outside it are never
retrieved, so they cannot leak through a snippet. When a link view is trimmed,
the platform says how many links it withheld.

**Identity is never silently merged.** Name matching auto-links above 0.90,
queues 0.72–0.90 for human review, and keeps every source row. Identities are
connected components over auto-links; nothing is irreversible.

**Scores describe records, not people.** The offender score summarises recorded
history — case count, offence variety, recency, gravity escalation, network
position — with every weight published. It is not a prediction, it is labelled
as such, and asking the platform to predict who will offend is refused before
routing.

---

## 4. Architecture

```
React console  ──►  FastAPI (43 operations)  ──►  SupervisorAgent
                                                      │
              ┌───────────────────────┬───────────────┴───────┐
              ▼                       ▼                       ▼
      DataRetrieval          CrimeAnalytics          NetworkIntelligence
        (SQL + RAG)        (trends, hotspots,      (graph, entity resolution,
                            early warning,          offenders, money flow)
                            forecasting)
              └───────────────────────┬───────────────────────┘
                                      ▼
                            InvestigationSupport
                       (briefings, timelines, priority)

Deterministic services (not agents): authorization · audit · evidence ·
memory · language · identity · PDF export
```

**Five agents, exactly.** Everything that decides who may see what, what counts
as evidence, or what goes in the audit log is ordinary code with ordinary
tests. `AGENT_ROSTER` is asserted by a test, and the intent routing table is
checked for exhaustiveness at construction: an intent with no route raises at
startup rather than defaulting silently. See
[ADR-0002](docs/adr/0002-five-agent-architecture.md).

### Layering

Ports and adapters, wired by hand in one composition root
(`interface/container.py`). There is no service locator and no runtime magic:
a component missing a dependency fails at startup, not mid-conversation.

| Layer | Contains | May import |
|---|---|---|
| `domain/` | errors, enums, value objects, models, ports | nothing (no I/O) |
| `application/` | agents, analytics, graph, RAG, NLU, pipeline, services | `domain` |
| `infrastructure/` | SQLite, Catalyst, Neo4j, LLM, language, embeddings, filestore | `domain` |
| `interface/` | container, FastAPI app, routers, schemas | everything |

Swapping SQLite for the Catalyst Data Store is one environment variable and one
factory function. The same is true of the file store, key/value store, cache,
identity provider and graph engine — five independent switches, because a
deployment should have to *state* what it is using rather than inherit it.

### Data flow

```
generate → land (NDJSON) → raw → curated → data-quality gate → intelligence
                                                 │
                                                 └─ a blocking failure halts the run
```

Hash-diff change detection (`ctl_row_hash`), batch bookkeeping
(`ctl_batch_log`), forward-only migrations (`ctl_schema_version`, now at
version 4), and an append-only audit trail.

---

## 5. Data model and provenance

49 tables in three families, and the prefix is load-bearing — it decides how a
statement built from the row is rendered.

| Prefix | Count | Origin | Rendered as |
|---|---|---|---|
| `curated_*` | 26 | The organiser's FIR schema, **column names unchanged** | source record, cited by CrimeNo |
| `cip_*` | 14 | Platform-derived (graph, scores, embeddings, hotspots, identities) | `(inferred)` where a link or score is derived |
| `ext_*` | 2 | **Not in the organiser's schema** — a platform extension | `(synthetic extension)` |
| `ctl_*`, `raw_*`, `audit_*` | 7 | Pipeline bookkeeping and the audit trail | not user-facing |

The organiser's schema is unchanged. The loader conforms to the real table
columns rather than the generator's ambitions: a generated column the schema
does not declare is dropped and logged, which is what keeps that guarantee
true over time.

The two `ext_` layers are **not equivalent**, and
[data-provenance-and-governance.md](docs/data-provenance-and-governance.md)
explains why:

- **`ext_financial_transaction`** — synthetic, and genuinely blocked. No public
  FIR-linked transaction dataset exists, and real ones are protected under the
  DPDP Act, the Banking Regulation Act, PMLA and RBI rules. The *analysis* runs
  unchanged on approved real data.
- **`ext_socioeconomic_indicator`** — calibrated against published sources
  (Census 2011, NSSO 68th round, Planning Commission BPL), recorded in
  `data_source`. Loading the real published tables with
  `data_quality='official'` drops the extension marker with zero code changes.

---

## 6. The analytics, and their formulas

Every figure the platform states is produced by one of these and returned with
its formula attached. Nothing here is prompted.

| Analysis | Formula | Guard |
|---|---|---|
| Trend | `slope = Σ(x−x̄)(y−ȳ) / Σ(x−x̄)²` over monthly counts | direction is reported *with* the slope, not instead of it |
| Hotspots | `intensity = cell_count / mean(count over occupied cells)`, 750 m grid, 90-day window | cells below `hotspot_min_cases` are not reported |
| Early warning | `z = (observed − mean(baseline)) / max(stddev(baseline), 1.0)` | the z-score floor stops a flat baseline manufacturing an alert |
| Seasonality | `z = (current − mean(prior years of same month)) / max(stddev, 1.0)` | a month with fewer than 2 prior years is `insufficient_history` and reports no figure |
| Event comparison | `z = (observed − mean(matched windows)) / max(stddev, 1.0)` | the result type has **no causal field**, and a test asserts it stays that way |
| Socio-economic correlation | `Pearson r = Σ(xi−x̄)(yi−ȳ) / √[Σ(xi−x̄)²·Σ(yi−ȳ)²]` | correlation is reported as correlation; the extension marker travels with it |
| Forecast | rolling-rate projection over the recent window | always a range with backtest error; refuses when history cannot support it |
| Spatio-temporal | `λ = (α·r₂ + (1−α)·r₁ + 0.15·Σ K(dᵢⱼ)·rⱼ) · horizon_days` | labelled a projection everywhere it surfaces |
| Offender score | `Σ component weights, capped at 100` | cases (≤40), sub-head variety (≤15), recency (≤20), gravity escalation (10), centrality (≤15) |
| Investigation priority | `Σ component weights, capped at 100` | scores the **case**, never a person; every weight and rationale returned |
| Entity resolution | weighted name and attribute similarity | ≥0.90 auto-link, 0.72–0.90 human review, conflicting initials demote to review |

---

## 7. Security and governance

**Roles and permissions.** Six roles map to a fixed permission set
(`ROLE_PERMISSIONS`). A capability check happens at the API boundary *and* at
the agent boundary; scope injection happens in the repository.

**Scope.** A `UnitScope` is folded into every query predicate. A caller who
forgets a capability check still cannot read rows outside their unit subtree.
Statewide roles are explicit, not implied.

**Field masking.** Identity fields are replaced with an explicit
`[withheld: aggregate-only role]` for roles without `read_person_identity`.
Caste and religion require `read_sensitive_demographics` and drive aggregates
only — an individual lookup on those dimensions is refused.

**File ownership.** `/files/{path}` enforces ownership from the key itself
(`exports/<user_id>/…`, `audio/<user_id>/…`), not merely authentication. A key
the platform cannot attribute to an owner is refused to everyone. Oversight
roles may read any artefact, and every such read is audited with the owner
recorded.

**Audit.** Append-only, correlation-ID stamped, ~7-year retention by default.
Every agent call, export and cross-user file read is recorded.

**Prohibited questions.** "Predict who will commit a crime" is refused before
routing, by a pattern that requires a forward-looking verb — so asking what a
person *did* stays answerable, and only asking what they *will do* is refused.

**Errors.** RFC 9457 problem documents throughout. Configuration errors name
the *setting*, never its value, so the output is safe to paste into a ticket.

---

## 8. How it is verified

```bash
python cip.py test                  # 593 passed, 9 skipped
python cip.py test -m "not slow"    # 439 passed in seconds, no seeding
```

38 test files: 28 unit, 10 integration. The 9 skips need either live Catalyst
credentials (behind an explicit opt-in flag and a Production guard) or a GPU.

**The validation loop is the important part.** Analytics over synthetic data
prove nothing unless you know what was planted. The generator plants known
signals and records them in a manifest:

- a **surge** in one district × crime type (Ballari / Online Financial Fraud),
- three geographic **hotspots** (Mysuru, Davanagere, Shivamogga),
- a **network ring** whose members appear under transliteration variants,
- a long tail of **repeat offenders**,
- a **financial burst**: one account given a spike of transfers on a known day
  against a deliberately quiet run-up.

`tests/integration/test_pipeline_and_data.py::TestPlantedSignalsAreDetected`
asserts the analytics recover each one. The hotspot cases are planted *inside*
the 90-day detection window on purpose; spreading them over the full period
would make the loop meaningless.

Beyond that: an agent evaluation corpus with routing and safety fixtures, a
schema-reflection parity test that fails if a migration adds a column the
Catalyst adapter's schema reader cannot see, contract tests over the Catalyst
adapters' ZCQL translation and escaping, and static consistency tests over the
Catalyst deployment descriptor.

---

## 9. Deploying to Zoho Catalyst

Every local module maps to a deployment component. Nothing is re-implemented
for the cloud.

| Local | Catalyst component | Source |
|---|---|---|
| FastAPI app (`main:get_app`) | **AppSail** `cip-api` (Python) running uvicorn | `catalyst/appsail/api/` |
| React console | **AppSail** `cip-console` (Node) serving the build and proxying `/api` | `catalyst/appsail/console/` |
| Pipeline stages | **Event Function** `cip_refresh` | `catalyst/functions/cip_refresh/` |
| Nightly orchestration | **Circuit** `cip_nightly` + **Cron** `cip_nightly_trigger` | `catalyst/circuits/nightly.json` |
| SQLite | **Data Store** (tables generated from the same `schema.sql`) | — |
| Local file store | **Stratus** bucket `cip-ingest` | — |
| In-process cache | **Cache** segment `cip_session` | — |

`cip-api` is an **AppSail service, not an Advanced I/O function**. The
Advanced I/O runtime has no documented ASGI bridge — confirmed both from
Zoho's documentation and from the CLI's own function-type enumeration — so a
handler returning the ASGI app object could never have worked. See
[catalyst-runtime.md](docs/deployment/catalyst-runtime.md).

### 9.1 Build the artifacts first

**Do not deploy `catalyst/appsail/*` directly.** Catalyst ships only the
directory it is given, and neither entrypoint is self-contained in the
checkout: the API needs the `ksp_cip` package, and the console needs
`frontend/dist`. Both live outside their own source directory. Staging fixes
that, and the build fails loudly if a piece is missing.

```bash
python cip.py package        # → dist/cip-api, dist/cip-refresh, dist/cip-console
```

That builds the console first if it has not been built, then stages all three.
To do one at a time, or to drive the builder directly:

```bash
python cip.py package api
python scripts/build_catalyst_artifact.py --target console
```

Each Python target is verified by importing its own module set with `sys.path`
containing *only* the staging directory, under `python -I`, so a pass is not an
accident of your shell. The console target is verified by checking that
`server.js` and `dist/index.html` are both inside the staged directory. A
`.manifest.json` with per-file SHA-256 is written next to each artifact.

`catalyst.json` records both paths per component: `source` (where the entrypoint
is maintained) and `deploy_source` (what to actually ship), with the `build`
command that produces it. A test asserts every component declares both and that
the named build target exists.

### 9.2 Provision

1. Create separate Catalyst **Development** and **Production** projects.
2. Provision Data Store tables from `backend/ksp_cip/infrastructure/db/schema.sql`
   plus the migrations in `migrations.py`. Do **not** run SQLite
   `executescript()` against Catalyst — use Catalyst table management. The
   container refuses to try. See
   [catalyst-schema.md](docs/deployment/catalyst-schema.md).
3. Create the Stratus bucket with prefixes `landing/`, `raw/`, `manifests/`,
   `exports/{user_id}/`, `audio/{user_id}/`.
4. Set the OAuth triple and project id in Catalyst configuration.
5. Run the read-only readiness checks, then the smoke tests, against
   Development.
6. Flip the backend selectors.

### 9.3 Deploy

```bash
catalyst login                          # browser OAuth; only the project owner can do this
catalyst project:use <project_id>
catalyst serve --only appsail:cip-api   # local emulator smoke pass, before anything goes live

catalyst appsail:add --name cip-api     --stack python_3_11 --source dist/cip-api \
                     --command "python3 -u server.py" --port 9000
catalyst appsail:add --name cip-console --stack node18      --source dist/cip-console \
                     --command "node server.js" --port 9000
catalyst functions:add --type event --stack python_3_11 --name cip_refresh

catalyst deploy --only appsail:cip-api
catalyst deploy --only appsail:cip-console
catalyst deploy --only functions:cip_refresh
```

Set `CIP_API_URL` on `cip-console` to the deployed `cip-api` URL. The proxy
selects `http` or `https` from the target's own scheme, because an internal
AppSail-to-AppSail call is not guaranteed to be TLS the way a public endpoint
is.

### 9.4 Configuration

Switching persistence is one variable per port:

```bash
KSPCIP_DATASTORE_BACKEND=catalyst
KSPCIP_FILESTORE_BACKEND=catalyst      # enforced: exports on a function disk vanish at cold start
KSPCIP_KEYVALUE_BACKEND=catalyst
KSPCIP_CACHE_BACKEND=catalyst
KSPCIP_IDENTITY_BACKEND=catalyst
```

`catalyst/_bootstrap.py` defaults the data store *and* the file store together,
because the settings validator rejects the mixed combination by design. Check a
configuration without starting the API:

```bash
python -m ksp_cip.cli config     # exits non-zero and names every problem at once
```

The full setting inventory is in [deployment.md](docs/deployment.md). **No
secret value appears in this repository, and none may be added to it.**

---

## 10. What is not verified

Stated plainly, because a deployment guide that reads as certain about things
nobody has run is worse than no guide.

- **This has never been run against a live Catalyst project.** The ZCQL
  translation and escaping are covered by contract tests; the network path is
  not. `catalyst login` is a browser OAuth flow only the project owner can
  complete.
- **Whether `python_3_11` and `node18` are available stack identifiers** in a
  real project. The `python_<major>_<minor>` naming convention was confirmed
  against the installed CLI's own help text; the enumerated list of installable
  versions was not.
- **The Cron block in `catalyst.json`** records provisioning intent (what to
  create, with which schedule and target), not a file the CLI ingests. Zoho
  publishes no committed-file schema for Cron.
- **The AI4Bharat speech service's model-loading code** has never run against
  real weights — no GPU was available. The HTTP contract is tested on both
  sides; the model calls are not.
- **The Neo4j graph adapter** has never run against a live Neo4j instance. It
  is contract-tested and falls back to NetworkX automatically.

---

## 11. Honest limitations

Each of these is surfaced in the product, not only in this file.

- **Kannada is an offline glossary, not a translator**, unless Bhashini or the
  self-hosted AI4Bharat service is configured. The platform reports
  `language_full_fidelity: false` and the console badges "kannada: offline
  glossary". Kannada *input* routes and scopes correctly today; output stays
  English-dominant. The self-hosted path needs hardware, not credentials.
- **NetworkX holds the graph in memory** — right for this scale, not statewide.
  `/capabilities` reports which engine is actually bound.
- **Grid binning is coarser than kernel density**; a cell boundary can split one
  real concentration in two, and the answer says so.
- **Seasonality needs two prior years per calendar month.** Below that a bucket
  is `insufficient_history` and reports no deviation, so a 24-month seed yields
  few reportable months. Intended, not a bug — and the "this is a historical
  comparison, not a forecast" disclaimer is attached even when nothing is
  reportable.
- **The ZCQL upsert emulation is not atomic.** `INSERT … ON CONFLICT` becomes
  read-then-write; concurrent writers can duplicate. Every caller is a
  replayable pipeline stage on a deterministic key. Never use it for a counter.
- **Event-window comparison is reachable through the API but not the chat
  classifier** — "compare crime during Dasara" currently falls through to
  general retrieval. Use `POST /analytics/event-comparison`.
- **A question the classifier cannot narrow returns an unfiltered case list**
  rather than asking what you meant.
- **Four capabilities use in-repo implementations where a Catalyst service
  exists** (text LLM, voice, PDF generation, tabular AutoML). Each needs the
  target project's own endpoint sample before it can be wired honestly; see
  [PENDING.md §B3](docs/PENDING.md).

---

## 12. Repository map

```
cip.py               THE entry point - install, seed, run, test, package
cip.bat              double-clickable Windows wrapper around cip.py
backend/
  ksp_cip/
    config/          Settings — every runtime knob, env prefix KSPCIP_
    domain/          errors, enums, value_objects, models, ports   [no I/O]
    application/
      agents/        the five agents + supervisor
      services/      deterministic: authorization, audit, evidence, memory,
                     language, identity, pdf_export
      analytics/     stats.py (pure) + engine.py + spatiotemporal + socioeconomic
      graph/         entity_resolution, builder, service (NetworkX), financial
      rag/           retrieval (ACL pre-filter, hybrid ranking)
      nlu/           rule classifier + slot extraction (17 intents)
      pipeline/      generators/, loader, dq, intelligence, orchestrator
    infrastructure/
      db/            schema.sql, sqlite_store, migrations, kv_store, repositories/
      catalyst/      datastore (ZCQL), stratus, nosql, cache, identity
      graph/         neo4j adapter
      llm/           gateway + providers (local deterministic default)
      language/      Bhashini, AI4Bharat, offline Kannada lexicon
      embeddings/, filestore/, observability/
    interface/
      container.py   composition root — everything wired here, by hand
      api/           main.py, deps.py, schemas.py, routers/ (11)
    resources/       prompts/ (versioned), lexicon/kn_en.json
    cli.py           seed | refresh | check | dq | serve | config
  tests/             unit/ (28 files) + integration/ (10 files) + evals/
frontend/            React + Vite console
catalyst/            catalyst.json, appsail/, functions/, circuits/, _bootstrap.py
speech-service/      self-hosted AI4Bharat ASR/TTS (needs a GPU)
scripts/             thin wrappers over cip.py (.sh and .ps1)
                     build_catalyst_artifact.py, generate_schema_manifest.py
docs/                adr/ 0001–0006, deployment/, provenance, forecasting, voice
```

---

## 13. Documentation index

| Document | Contents |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Engineering memory — layout, status, gotchas, how to resume cold |
| [`docs/PENDING.md`](docs/PENDING.md) | What is left, why it is blocked, and what unblocks it |
| [`docs/deployment.md`](docs/deployment.md) | Every setting name and what selecting it commits you to |
| [`docs/deployment/catalyst-runtime.md`](docs/deployment/catalyst-runtime.md) | Why AppSail and not Advanced I/O; what was checked |
| [`docs/deployment/catalyst-schema.md`](docs/deployment/catalyst-schema.md) | Data Store provisioning as a reviewed step |
| [`docs/deployment/v3-catalyst-commands.md`](docs/deployment/v3-catalyst-commands.md) | CLI commands actually run, and their real output |
| [`docs/data-provenance-and-governance.md`](docs/data-provenance-and-governance.md) | The two `ext_` layers, and why they are not equivalent |
| [`docs/forecasting.md`](docs/forecasting.md) | The forecasting method and its guards |
| [`docs/voice-ai4bharat.md`](docs/voice-ai4bharat.md) | Self-hosted Kannada ASR/TTS, degradation rules, audio ownership |
| [ADR-0001](docs/adr/0001-hackathon-production-reconciliation.md) … [ADR-0006](docs/adr/0006-analytics-are-computed-not-prompted.md) | The six architectural decisions and their consequences |
| [`COMPARISON_REPORT.md`](COMPARISON_REPORT.md) | Local build vs the live deployment, 24 Aug 2026 |

API documentation is served at `/api/v1/docs` when the platform is running.
