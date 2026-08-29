# Catalyst service coverage — submission compliance

The challenge rules require Catalyst deployment and state that *"using a
third-party alternative when a Catalyst service is available may affect the
validity of your submission."* This file maps all 26 listed capabilities to
what this repository actually does, and — for anything not yet on the
Catalyst service — the exact provisioning step to close it.

**Read the status column literally.** "Adapter written, never run live" does
not mean working. Nothing in this table has been executed against a live
Catalyst project.

---

## 1. Coverage table

| # | Capability | Required service | Status |
|---|---|---|---|
| 1 | Serverless functions | Functions | Declared: `cip_refresh` (`type: event`) |
| 2 | Docker/OCI image | AppSail custom OCI | **N/A** — managed runtime used instead (#3) |
| 3 | Web app, managed runtime | AppSail managed | Declared: `cip-api`, `cip-console` |
| 4 | Frontend / SPA / static | Slate *or* Web Client Hosting | **Gap** — console is served by an AppSail Node process |
| 5 | Custom domain + SSL | Domain Mappings | **Gap** — not configured |
| 6 | Relational database | Data Store | Adapter written, never run live |
| 7 | Unstructured data | NoSQL | Adapter written, never run live |
| 8 | Object storage | Stratus | Adapter + `cip-ingest` bucket declared |
| 9 | Cache | Cache | Adapter + `cip_session` segment declared |
| 10 | Full-text search in Data Store | Data Store | **Gap** — uses in-repo hashed n-gram TF-IDF |
| 11 | Text LLMs / RAG | **QuickML** | **Gap (priority)** — local provider; Anthropic/Gemini/Groq optional |
| 12 | No-code ML pipelines | QuickML | **N/A** — no no-code pipeline in scope |
| 13 | AutoML (tabular) | Zia AutoML | **Gap** — forecasting is an in-repo Poisson model |
| 14 | OCR / Face / Vision | Zia Services | **N/A** — no image input in scope |
| 15 | Voice STT / TTS / translation | **Zia Services** | **Gap (priority)** — browser Web Speech + self-hosted AI4Bharat |
| 16 | PDF / report generation | **SmartBrowz** | **Gap (priority)** — uses `reportlab` in-process |
| 17 | User auth | **Authentication** | Backend verifier wired; **frontend SDK missing** |
| 18 | API routing / throttling | API Gateway | **Gap** — not enabled |
| 19 | OAuth to 3rd-party Zoho | Connections | **N/A** — no third-party Zoho calls |
| 20 | Scheduled jobs / cron | Cron *or* Job Scheduling | Declared: `cip_nightly_trigger` cron → `cip_nightly` circuit |
| 21 | React to in-project events | Signals + Event Functions | Partial — `cip_refresh` is `type: event`; no Signal bound |
| 22 | Cross-app event bus | Signals | **Gap** — no Signal configured |
| 23 | Multi-step workflow | Circuits | Declared: `cip_nightly` |
| 24 | Transactional email | Mail | **N/A** — no email feature |
| 25 | Push notifications | Push Notifications | **Gap** — early-warning alerts are pull-only today |
| 26 | CI/CD | Pipelines | **Gap** — `catalyst-pipelines.yaml` not authored |

---

## 2. Why several of these are not committed as config files

Catalyst configures API Gateway, Domain Mappings, Cron, Push Notifications
and Pipelines through the **console or CLI**, and Zoho does not publish a
repository-committed schema with field names for them. `catalyst-pipelines.yaml`
*is* a real committed file, but its schema is authored in the console's YAML
editor/Assistant and the full field reference is not public either.

Writing speculative JSON/YAML for those would produce files that look wired
and silently do nothing — strictly worse than not having them. So the items
below are recorded as **provisioning steps to run against a live project**,
not as invented config. Each says what is verified and what is not.

The one exception is Cron, whose *semantics* are documented well enough to
declare intent safely — see §3.

---

## 3. Cron and Circuits — a defect this pass fixed

**Verified from the Catalyst Cron documentation:** a cron's *schedule point*
may be a function, a third-party URL, or a **Circuit**; schedule types are
*One Time* and *Recursive*; the minimum recursive frequency is one hour.

`circuits/nightly.json` previously carried its own top-level
`"schedule": {"cron": "0 2 * * *"}` block. **A Circuit has no schedule field
— it is invoked by a Cron.** That block would never have fired: the nightly
pipeline would simply never have run on a real deployment.

The schedule now lives on `cip_nightly_trigger` in `catalyst.json`, targeting
the `cip_nightly` circuit. Field names in that block are the reviewed intent
(what to create, on what schedule, against which target); replace them with
the real console field names once the cron exists.

---

## 4. Provisioning steps for the remaining gaps

Commands marked **confirmed** were verified against the installed CLI 1.27.0
(see `v3-catalyst-commands.md`). All require `catalyst login` first, which
only the project owner can perform.

### #18 API Gateway
```bash
catalyst apig:status          # confirmed command
catalyst apig:enable          # confirmed command
```
Then, in the console, add rules routing `/api/v1/*` to the `cip-api` AppSail
service, and apply throttling. Rule fields (path, method, target, rate limit)
are console-side; record the real shape here once created.

### #4 Slate / Web Client Hosting
```bash
catalyst slate:create         # confirmed command
catalyst slate:link
```
`frontend/dist` is already a plain static build, so serving it from Slate is a
target change, not a rewrite — it removes the `cip-console` Node process
entirely. Keep `/api` pointed at the `cip-api` service.

Two notes added 29 Aug 2026:

- **Slate also removes a real defect rather than only a process.** `server.js`
  read its document root from outside the directory Catalyst ships, so
  `cip-console` would have deployed and served nothing. That is fixed
  (`resolveDist()` plus a `--target console` artifact), but a static host has
  no document root to get wrong at all.
- **The `/api` path is the thing to plan for.** `cip-console` currently gives
  the browser a single origin by proxying. Slate does not proxy, so moving
  there means either an API Gateway route (#18) putting `/api/v1/*` on the
  same origin, or setting `KSPCIP_CORS_ORIGINS` to the Slate origin and
  letting the console call `cip-api` cross-origin. The first is preferable —
  the console's API client assumes a same-origin `/api/v1` base and the
  WebSocket voice stream derives its URL from `window.location`.

### #5 Domain Mappings
Console → Domain Mappings → add the domain and complete DNS/SSL validation.
No CLI equivalent and no committed file.

### #26 CI/CD Pipelines
Create `catalyst-pipelines.yaml` **from the console's YAML editor/template**
(it links the Git repo and triggers on push), then commit the generated file.
Stages needed: install deps → `pytest` (the suite is the real gate) → deploy
AppSail + functions. Do not hand-write this file blind.

### #21 / #22 Signals + Event Functions
`cip_refresh` is already `type: event`, so the function half exists. Bind a
Signal to the Data Store insert events that should trigger an incremental
refresh, instead of relying only on the nightly cron.

### #25 Push Notifications
The natural fit is the early-warning alerts, which are pull-only today
(`GET /analytics/early-warning`). Pushing an alert on creation is a genuine
product improvement, not just a checkbox — but it needs the project's push
credentials and a device/web registration flow.

### #11 QuickML, #15 Zia voice, #16 SmartBrowz, #13 Zia AutoML, #17 Auth frontend
These need live project details and are tracked separately — they change
application code, not just deployment config. For QuickML the auth header
shape is known (`X-QUICKML-ENDPOINT-KEY`, `Authorization: Zoho-oauthtoken …`,
`CATALYST-ORG`, `Environment`; OAuth scope `QuickML.deployment.READ`) but the
request/response **body** is per-deployment and only shown on the endpoint's
console page — that sample is the missing piece, not the concept.

Catalyst Authentication is not a backend-only change: the Web SDK runs in the
console (React) and hands the app a Catalyst-issued JWT.
`infrastructure/catalyst/identity.py` already verifies such a token and maps
it to a `Principal`, and the API now routes verification through the
configured provider — but nothing issues that token until the frontend SDK is
wired with the project's client config.

---

## 5. Honest summary

Deployment-shaped work is done: components are declared, adapters exist for
every data-plane service, and the orchestration graph is real. What is **not**
done is (a) anything ever running against a live project, and (b) four
capabilities still served by non-Catalyst implementations (#11, #13, #15, #16)
plus the frontend half of #17.

Items #4, #18, #21, #22, #25, #26 are console/CLI provisioning that can be
completed quickly once logged in; they are gaps in configuration, not in the
application.
