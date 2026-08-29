# Pending work and blockers

Status as of **29 Aug 2026**. Every item says **what** is left, **why** it is
blocked, and **who or what unblocks it**. Anything not listed here is done and
covered by the test suite (**593 passing, 9 skipped** — the 9 skips are
live-Catalyst and GPU tests that require credentials/hardware).

Ordered by what blocks what, not by size.

---

## A. Source control and local correctness — **resolved**

### A1–A2. The nine bug fixes are committed
They are in `main` as `c978319` ("Fix nine defects across product, Catalyst
adapters and deployment descriptor"), merged by `8927909`. The missing-branch
question in the previous revision of this file is moot. **No action needed.**

### A3. The 14 post-deployment fixes — **all verified done**
Confirmed present in code *and* exercised live against the running API:
individual-prediction refusal (refuses pre-routing), `FORECAST_QUERY` and
`SPATIOTEMPORAL_QUERY` (both render their own payload types), plural
"networks" boundary fix, relational name extraction, early-warning alert
cards, authenticated PDF blob download, chart bottom margin (62px),
environment-gated demo accounts, financial overview branch, and the
sensitive-demographics graceful refusal. **No action needed.**

### A4. Twelve further defects found and fixed, 29 Aug 2026
Full table in [`CLAUDE.md` §10](../CLAUDE.md). The four that mattered most:

- **The checked-in database had zero user accounts.** 4,200 cases, 26k graph
  edges, and nobody who could sign in — a seed interrupted after the
  intelligence refresh. A fresh clone was unusable. Re-seeded; the tail of the
  pipeline is now asserted by `tests/integration/test_seed_reset.py`.
- **`seed --reset` had never worked.** `curated_ComplainantDetails` was missing
  from the truncation list, so every reset died on a foreign key.
- **`cip-console` would have deployed and served nothing.** `server.js` read
  the console build from `frontend/dist`, outside the directory Catalyst
  ships. There is now a `console` artifact target, and `catalyst.json`
  separates `source` from `deploy_source`.
- **A first Catalyst deploy always failed at startup.** `_bootstrap.py`
  defaulted the data store to Catalyst but left the file store on `local`, a
  combination the settings validator rejects by design. Both now default
  together.

### A5. One entry point, 29 Aug 2026
There were three front doors (`scripts/*.sh`, `scripts/*.ps1`, and the
application CLI), none runnable by double-click on Windows, each with its own
copy of the install logic. `cip.py` at the repository root is now the single
entry point — `python cip.py` installs, seeds if needed, and serves — with
`cip.bat` as a double-clickable Windows wrapper and the shell scripts reduced
to thin wrappers over it. `python cip.py doctor` reports what state a checkout
is in and names the one command that fixes the first problem it finds;
`python cip.py package` builds the Catalyst artifacts.

It never imports `ksp_cip` and never builds the app: `get_app()` stays the only
application factory, and Catalyst runs `server.py`, not the launcher. Pinned by
`tests/unit/test_launcher.py`.

**No action needed** — but re-read `CLAUDE.md` §6 before touching the seed
pipeline, the seasonal/forecast refusal gates, or the Catalyst entrypoints.

---

## B. Blocked on Catalyst credentials

### B1. Deploy to the live project
Nothing in section A reaches the live site until deployed. The live AppSail
currently runs a **stale build that fails ~7 of 10 capability areas** and
answers the prohibited "predict who will commit a crime" query instead of
refusing it (see `COMPARISON_REPORT.md`). Redeploying is the single
highest-impact action available.

**Build the artifacts first — this changed on 29 Aug.** Deploying
`catalyst/appsail/*` directly ships an entrypoint without the code it needs.
Stage all three, then point `appsail:add` at `dist/`:

```bash
python cip.py package     # builds the console if needed, then stages all three
```

Each target self-verifies and writes a SHA-256 manifest; the console target
fails the build if `frontend/dist` has not been built. `catalyst.json` records
the `build` command and `deploy_source` per component.

**Blocker:** `catalyst login` — browser OAuth against a real Zoho account.
Only the project owner can do this; it cannot be scripted or delegated.

### B2. Re-seed the live Data Store to ~2,000 cases
Live currently holds ~100 cases, which is why hotspots come back empty there.
Procedure: `docs/deployment/refresh-live-deployment.md`.
**Blocker:** project OAuth credentials (client id/secret/refresh token).

### B3. Four capabilities still on non-Catalyst implementations
The rules state that using a third-party alternative where a Catalyst service
exists *"may affect the validity of your submission"*. These are the
submission-validity risks:

| Capability | Required | Currently | What is actually missing |
|---|---|---|---|
| Text LLM / RAG | **QuickML** | local deterministic provider | The endpoint's **request/response body**. Auth headers are known (`X-QUICKML-ENDPOINT-KEY`, `Authorization: Zoho-oauthtoken …`, `CATALYST-ORG`, `Environment`; scope `QuickML.deployment.READ`) but the body schema is per-deployment and shown only on the endpoint's console page. |
| Voice STT/TTS | **Zia Services** | browser Web Speech | Which Zia voice service, and its endpoint + sample payload from the console. |
| PDF generation | **SmartBrowz** | `reportlab` in-process | SmartBrowz API details. Note the current PDF works well and is evidence-bound; this is a compliance swap, not a repair. |
| AutoML (tabular) | **Zia AutoML** | in-repo Poisson model | Whether forecasting must move, or whether the documented in-house model is acceptable. Worth confirming before rewriting working, explainable analytics. |

**Blocker for all four:** live project + the console's own endpoint samples.
I will not guess these schemas — a provider that looks wired but silently
fails is worse than none.

### B4. Catalyst Authentication — frontend half
The backend is done: `infrastructure/catalyst/identity.py` verifies a
Catalyst JWT and maps it to a `Principal`, and the API now routes verification
through the configured provider (this was broken until today — `deps.py` called
the local service directly, so `KSPCIP_IDENTITY_BACKEND=catalyst` silently did
nothing).
**Still missing:** the React console has no Catalyst Web SDK wiring, so nothing
ever issues a Catalyst token.
**Blocker:** the project's client config / `catalyst-config.json`.

### B5. Console/CLI provisioning — quick once logged in
Not code changes. Details and confirmed commands in
`docs/deployment/catalyst-service-coverage.md`.

- **#18 API Gateway** — `catalyst apig:enable` (confirmed command), then route
  `/api/v1/*` → `cip-api` and set throttling in the console.
- **#4 Slate / Web Client Hosting** — `catalyst slate:create` (confirmed).
  **Cheap win:** `frontend/dist` is already a plain static build, so this
  *deletes* the `cip-console` Node process rather than requiring a rewrite.
- **#5 Domain Mappings** — console only, plus DNS/SSL validation.
- **#26 CI/CD Pipelines** — generate `catalyst-pipelines.yaml` from the
  console's YAML editor, then commit it. Gate on `pytest`. Do not hand-write
  it blind; the field schema is not published.
- **#21/#22 Signals** — `cip_refresh` is already `type: event`; bind a Signal
  to Data Store inserts for incremental refresh.
- **#25 Push Notifications** — natural fit is early-warning alerts, which are
  pull-only today (`GET /analytics/early-warning`).

### B6. Cold start and concurrency
The live service serialises requests (~5s under concurrency) and drops the
first request after idle.
**Fix:** raise the AppSail instance/worker count, and warm the service before
any demo. **Blocker:** console access.

---

## C. Blocked on compute, not code

"Compute" means a machine you must actually have — not a credential you can
buy. C1 is a genuine hardware constraint; C2 is an untested external service
(earlier filed under "hardware", which overstated it).

### C1. Kannada full fidelity and server-side voice
`language_full_fidelity` is `false`. Kannada **input** works today via the
offline glossary (verified: a Kannada query correctly routes and scopes), but
**output stays English-dominant** and there is no server-side ASR/TTS.
**Unblocked by:** running `speech-service/` on a GPU box and setting
`KSPCIP_LANGUAGE_PROVIDER=ai4bharat`. Concretely that means an NVIDIA/CUDA
machine loading `indic-conformer-600m` (ASR), `indic-parler-tts` (TTS) and two
200M IndicTrans2 models, plus PyTorch/transformers (several GB) and `ffmpeg`
on PATH. The weights are free and open — **no API key exists that substitutes
for the machine.** On CPU a 600M conformer is far too slow for interactive
voice.

**Cheaper route worth checking first (see B3):** if Catalyst **Zia Services**
covers Kannada speech-to-text/text-to-speech, using it removes this hardware
blocker *and* satisfies compliance item #15 in one move. Confirm Zia's Kannada
support before committing to a GPU box.
**Never verified:** the AI4Bharat model-loading code has never run against
real weights. The HTTP contract is tested on both sides; the model calls are
not. The Kannada policing-vocabulary benchmark is built but **unrun**.

### C2. Neo4j graph adapter
`Neo4jGraphAdapter` is complete and contract-tested, with automatic fallback
to NetworkX and a zero-code switch (`KSPCIP_GRAPH_BACKEND=neo4j`).
**Never run against a live Neo4j instance.**
**Unblocked by:** any reachable Neo4j — a local Docker container is enough, so
this is an untested dependency rather than a hardware constraint. Only matters
at statewide scale; NetworkX is in-memory and correct at hackathon scale.

---

## D. The two `ext_*` layers — deliberate, and not equivalent

Full reasoning in [`data-provenance-and-governance.md`](data-provenance-and-governance.md).
The two are in different positions; treating them as one item hides that.

### D1. Financial — genuinely blocked, keep synthetic
No public dataset exists, and real FIR-linked bank transactions are protected
under the DPDP Act, Banking Regulation Act, PMLA and RBI rules. Nothing
unblocks this before a deadline; do not improvise a substitute. The analysis
(chains, concentration, bursts, amount bands, network position) is complete
and runs unchanged on approved real data.

**Done this pass:** `is_extension` is now read from the row instead of
hard-coded, so an approved ingestion becomes a data change rather than a code
change — matching the socio-economic layer. Rule is one-directional: an
aggregate is an extension if *any* contributing row is, and an unlabelled row
counts as one. Pinned by `TestExtensionMarkerIsDerivedFromData`.

### D2. Socio-economic — **not actually blocked**
The values are already calibrated against *published* sources (Census 2011
KA-08, NSSO 68th round, Planning Commission BPL), recorded in `data_source`.
Those are public aggregate statistics — no private data, no data-owner
sign-off needed.

Provenance is already read from the data, so loading rows with
`data_quality='official'` drops the `(synthetic extension)` marker with **zero
code changes**.

**Blocker:** someone supplying the actual published tables (31 districts × 13
indicators). **Do not reconstruct these from memory** — figures stamped
`data_quality='official'` that are half-remembered are strictly worse than
honestly-labelled approximations.

**Worth knowing before spending the effort:** the crime data is still
synthetic, so a real-census correlation is a demonstration that the pipeline
works, *not* a finding about Karnataka. Modest value; do it only if the tables
are already to hand.

### Do not "fix" D1 by wiring real data without sign-off.

---

## E. Accepted limitations — stated, not hidden

Documented in `CLAUDE.md` and surfaced in the product. Listed so nobody
re-discovers them as bugs.

- **NetworkX is in-memory** — right for this scale, not statewide (see C2).
- **Grid binning is coarser than kernel density** — a cell boundary can split
  one real concentration; the answer says so.
- **ZCQL upsert is not atomic** — translated to read-then-write. Every caller
  is a replayable pipeline stage on a deterministic key. Never use it for a
  counter.
- **Seasonality needs two prior years per calendar month** — below that a
  bucket reports `insufficient_history`. Intended, not a bug.
- **`sample_stddev` on very short histories** — the z-score floor (1.0) does
  most of the work.
- **Event-window comparison has an endpoint but no intent.** "Compare crime
  during Dasara with the baseline" classifies as `GENERAL_QA` and falls
  through to case retrieval; `POST /analytics/event-comparison` works. Adding
  an intent means a new `Intent` member, a classifier rule, a weight that does
  not steal from `SEASONAL_QUERY`, an agent branch and eval fixtures — worth
  doing deliberately rather than as a one-line addition.
- **An unclassifiable question returns an unfiltered case list.** "asdf qwerty"
  answers "4,200 FIRs match that description", which is true of the filter
  (there is none) but reads as a match. Asking what the user meant would be
  better; changing it touches the routing eval corpus.

---

## Suggested order

0. **Commit the 29 Aug hardening pass (A4).** It is in the working tree, not in
   version control. It includes the fix that makes a fresh clone usable at all,
   and the one without which the console deploys empty. Stage deliberately —
   `backend/var/ksp_cip.db` is a tracked 85 MB artifact and re-seeding rewrites
   all of it, which is worth a deliberate decision rather than `git add -A`.
1. **B1 + B2** — build the artifacts, deploy, re-seed. Biggest visible
   improvement, since live is running a stale build that fails a safety check.
2. **B5** — API Gateway, Slate, Signals, Pipelines. Fast once logged in.
   Note that **Slate deletes the `cip-console` Node process entirely** —
   `dist/cip-console/dist` is already a plain static build, so that is the
   cheapest item on this list.
3. **B3 + B4** — the four service swaps and the auth frontend. Most code
   churn; do them once the endpoint samples are in hand.
4. **C1** — only if a GPU box is available before the deadline.

### Worth a decision, not a fix

`backend/var/ksp_cip.db` is tracked in git: an 85 MB generated artifact whose
diff is unreviewable and which was, until this pass, silently wrong. Keeping it
buys "clone and run without seeding". The alternative is to untrack it and let
`seed` build it, which is one extra documented command and roughly 100 seconds.
Either is defensible; drifting between them is not, which is why it is written
down here.
