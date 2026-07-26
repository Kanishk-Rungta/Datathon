# KSP-CIP Implementation V2

## Purpose, scope, and non-negotiable deployment rule

This document is the V2 implementation baseline for the Karnataka State Police
Crime Intelligence Platform (KSP-CIP). It has two jobs:

1. Record what the current repository already implements, how the parts fit
   together, and why its safety boundaries are deliberately designed as they
   are.
2. Give an incremental, Catalyst-first plan for completing the remaining
   hackathon integrations and for taking the same codebase toward a controlled
   pilot. The plan must preserve the current tested behavior wherever possible.

The authoritative product constraints are the supplied hackathon implementation
plan and architecture document. Where the production architecture asks for
PostgreSQL, Neo4j, self-hosted GPU inference, or an on-premise Sync Agent, the
hackathon constraint wins: **the application platform, persistence,
orchestration, web hosting, authentication, cache, jobs, and file storage must
use Zoho Catalyst.** Do not introduce Firebase, Supabase, AWS, GCP, Azure,
Vercel databases, or a parallel backend. They would split the system of record
and violate the Catalyst-only requirement.

The two intentionally external integrations are also constrained:

- **Bhashini** is the government-operated Indian language service specified by
  the plan for Kannada ASR/NMT/TTS. It is accessed only through the existing
  `LanguageService` port and never becomes a data store.
- **An LLM provider** may be used only through `LLMGateway`, for intent
  disambiguation and controlled prose. The LLM never receives authority to
  retrieve records, decide access, calculate figures, or create facts. In a
  sensitive deployment, legal approval and a redaction/data-residency review
  are mandatory before any case text leaves Catalyst.

All data in the present build is synthetic. A real-source connector is a later,
separately approved workstream; it must not be silently enabled by V2.

---

## 1. Current system: completed work and integration model

### 1.1 What has been completed

The repository is a working, local-first crime-intelligence application with a
Catalyst deployment shell. It is not merely a frontend mock-up. It contains a
synthetic FIR dataset, pipeline, authorization model, conversational controller,
analytics, graph intelligence, evidence enforcement, audit trail, FastAPI API,
and React console.

The completed functional capabilities are:

- Synthetic, referentially consistent FIR-style records across the supplied
  curated schema, including masters, FIRs, accused, victims, complainants,
  sections, arrests, chargesheets, employees, and courts.
- A raw/landing -> curated -> intelligence pipeline with batch metadata,
  data-quality checks, refreshable derived views, and a manifest of deliberately
  planted signals.
- English conversational retrieval; limited offline Kannada terminology support;
  optional Bhashini adapter; browser speech-to-text input; deterministic
  multi-turn context; and conversation/case PDF export.
- Trend analysis, geo-grid hotspots, early-warning anomaly alerts, demographic
  aggregate analysis, similar-case retrieval, case summaries and timelines.
- Entity resolution without destructive merges, graph edges, community
  detection, path/ego-network expansion, centrality, repeat-offender-history
  scoring, and a human review queue.
- A clearly marked synthetic financial-transaction extension and money-flow
  analysis. It is never represented as part of the organiser's FIR schema.
- Evidence-bound claims, computation traces, inferred/synthetic markers,
  scope-aware record retrieval, role-based permissions, append-only auditing,
  owned-export access, correlation IDs, RFC 9457 problem responses, and tests.

The implementation is described in `README.md`, `CLAUDE.md`, the six ADRs, and
the generated schema dictionary. The code is the final authority if a document
does not agree with it.

### 1.2 The deliberate layering

The design is intentionally layered. This is what allows V2 to add Catalyst
services without rewriting business logic.

```text
React console / Catalyst AppSail host
                |
FastAPI routers (HTTP validation, auth dependency, response schemas)
                |
Application services and five-agent supervisor
                |
Domain models, errors, value objects, ports               <-- no I/O
                |
Infrastructure adapters: SQLite/Catalyst, File Store, LLM,
language, embeddings, observability
```

The composition root is `backend/ksp_cip/interface/container.py`. It constructs
the repositories, deterministic services, agents, pipeline, and adapters once.
The rule for every V2 change is: **add an adapter or small service behind an
existing port, bind it in the container, and keep domain/application callers
unchanged.** This minimizes regression risk and keeps local tests useful.

### 1.3 Data lifecycle and why it is safe

```text
Synthetic generator or future approved source
  -> NDJSON landing files
  -> batch/control records
  -> schema-conforming curated FIR tables
  -> DQ gate (blocking failure halts publication)
  -> intelligence refresh
       -> entity links and identities
       -> graph edges and centrality
       -> embeddings
       -> hotspot cells and early-warning alerts
       -> case-priority and recorded-history scores
  -> read-only conversational/API views
```

The loader uses the source table columns as the authority. It does not widen
the organiser schema when a generator happens to produce an extra field. CIP
enrichment fields use the `cip_` prefix and extension tables use `ext_`; this
makes it possible to prove which data came from the source schema and which
data is platform-derived.

The DQ gate is important. An analytics feature is unsafe if it computes a
perfect-looking chart from an incomplete, duplicated, or invalid batch. V2
must preserve the sequence `load -> DQ -> intelligence refresh`, must make each
step replayable, and must prevent a blocking DQ failure from publishing new
derived intelligence.

### 1.4 Conversational path

One user turn follows this controlled path:

1. The API authenticates the caller and constructs a `Principal` containing
   role, permissions, and permitted unit scope.
2. `SupervisorAgent` normalizes language, loads deterministic session memory,
   resolves references such as “that FIR” or “him”, classifies intent, and
   extracts slots.
3. The supervisor routes the request to exactly the specialist agent that owns
   that intent, with limited, explicit follow-up routing for case briefings and
   graph enrichment.
4. The specialist uses repositories and deterministic analytics/graph/RAG
   services. Scope is passed into the query/traversal, rather than filtering
   results after retrieval.
5. The `AnswerComposer` merges claims and rejects an unsupported numeric or
   inferred claim. The optional LLM can only polish the already-derived answer;
   a verifier rejects a rewrite that drops citations or adds facts.
6. The answer, citations, trace, structured payload, and audio request outcome
   are returned. The turn is stored, the action is audited, and the client
   renders citations/charts/graph/timeline.

This flow is deliberately more restrictive than a generic chatbot. It prevents
the dangerous failure mode where a model improvises a plausible police fact.

### 1.5 The five-agent system and current responsibilities

There are exactly five agents. The names refer to bounded responsibilities, not
five uncontrolled LLM personas.

| Component | Responsibility | Inputs it may trust | Output contract | Safety boundary |
|---|---|---|---|---|
| `SupervisorAgent` | Conversation controller, routing, memory coordination | Normalized input, session state, NLU result, principal | `Answer` | Cannot create factual claims itself. |
| `DataRetrievalAgent` | FIR/person/location/status lookup and similar-case retrieval | Safe slots, `UnitScope`, repositories/RAG | Evidence-bound records/tables | SQL scope predicates are applied before retrieval. |
| `CrimeAnalyticsAgent` | Trends, hotspots, early warning, demographic aggregates | Aggregate engine and authorized scope | Chart/table + computation trace | Counts are arithmetic, never model-generated. |
| `NetworkIntelligenceAgent` | Identity-aware links, graph paths, communities, recorded history, money flow | Graph/identity repositories and scope | Graph payload + edge/person/transaction evidence | Derived links are labelled `(inferred)`; money is labelled synthetic. |
| `InvestigationSupportAgent` | Briefings, timeline, case priority, similar-record pointers | Case children, graph, retrieval, engine | Timeline/briefing + cited leads | Leads are explicitly records to review, never accusations. |

Supporting services are intentionally not agents: authorization, memory, audit,
language, identity, evidence composition, PDF export, analytics statistics,
and pipeline/DQ work are deterministic code. That distinction is essential for
police accountability: policy enforcement and arithmetic cannot be delegated to
probabilistic prompting.

### 1.6 Agent correctness rules that must remain true in V2

V2 work is accepted only if all of the following remain true:

1. **No LLM is a source of truth.** Facts originate in records or deterministic
   computations. The LLM Gateway may be disabled without changing the factual
   answer.
2. **No claim without evidence.** A cited locator must resolve to a record,
   aggregate, edge, alert, person identity, or transaction that supports the
   claim. Negative findings need a query/aggregate locator too.
3. **Inference is visible.** Entity/graph-derived claims are marked inferred;
   financial-extension claims are marked synthetic; visual edges preserve the
   same distinction.
4. **Authorization is a retrieval constraint.** The query and graph expansion
   receive scope. Do not fetch statewide rows and hide them in UI code.
5. **Identity resolution is reversible.** A match creates a link and decision,
   not a destructive merge of source accused rows.
6. **Scores describe records and cases.** Individual historical scores cannot
   be presented as forecasts of future conduct. Case priority is transparent,
   component-based, and contestable.
7. **Every mutation is idempotent or replayable.** Catalyst Data Store lacks a
   general multi-row transaction primitive; pipeline steps therefore need batch
   identifiers, status transitions, and safe re-runs.

### 1.7 Existing Catalyst integration seam

The repository already has the correct first seam for Catalyst:

- `DataStore` is a port implemented by SQLite locally and `CatalystDataStore`
  for ZCQL.
- `FileStore`, `KeyValueStore`, `LanguageService`, `LLMGateway`, and
  `EmbeddingModel` are ports. Their callers do not need to know the provider.
- Catalyst descriptors map the API to an Advanced I/O function, refresh to an
  event function, UI hosting to AppSail, landing/exports to Stratus, and a
  nightly pipeline to a Circuit.
- `KSPCIP_DATASTORE_BACKEND=catalyst` selects the Catalyst datastore at the
  container boundary.

The adapter deliberately handles ZCQL differences (escaped scalar binding,
pagination, read-then-write behavior) instead of leaking those differences
into agents or repositories. This is exactly the compatibility mechanism V2
must use elsewhere.

---

## 2. Current limitations and V2 completion definition

The current code is a complete local demonstration, but the following areas are
not complete, not live-tested, or deliberately reduced in fidelity.

### 2.1 Catalyst runtime and platform services

The descriptors exist, but no evidence shows a live Catalyst project has been
provisioned and exercised. Data Store, Stratus, AppSail, Circuit, functions,
cache, authentication, and deployment environment behavior therefore require
real integration tests.

### 2.2 Language and voice

The Bhashini adapter exists but has no configured credentials in the default
environment. Default Kannada is a small domain glossary, not full translation.
The console uses browser dictation when available. The adapter can synthesize
audio, but the chat response/UI does not yet return, store, cache, or play TTS
audio. The seeded dataset does not presently demonstrate a populated Kannada
free-text corpus.

### 2.3 Catalyst-native identity, memory, cache, and files

Demo JWT users, SQLite-backed conversation records, relational KV storage, and
local files are useful locally but do not satisfy live Catalyst Authentication,
NoSQL/Cache, and Stratus operation. These should be replaced only at their
adapter boundaries, not in agents.

### 2.4 Analytics scope gaps

The platform has trend, hotspot, and anomaly alert analytics. It does not yet
offer a first-class seasonal-analysis endpoint or event-calendar analysis.
Sociology is a carefully limited complainant aggregate breakdown; it is not a
validated model of urbanization, migration, education, or economic stress.
Early warning detects anomalous recorded patterns; it is not a forecast model.

### 2.5 Semantic search and entity-quality gaps

The current offline hashed n-gram embedding is deterministic and suitable for
the synthetic demo. It is not a validated multilingual semantic embedding
system. Entity-resolution thresholds are safe and reviewable, but they have not
been calibrated against a labelled real-world evaluation set.

### 2.6 Production governance gaps

The application implements valuable guardrails, but a live police deployment
still requires Catalyst Auth/SSO/MFA decisions, secret handling, gateway rate
limits, monitoring, formal retention verification, security testing, privacy
review, backup/restore drills, and legal approval for language/LLM providers.

### 2.7 Required V2 workstreams with approval gates

V2 must plan for the following capabilities. They are not to be represented as
complete until their data contract, governance, implementation, and evaluation
gates have all passed:

- real financial-system ingestion and transaction link analysis;
- approved external socio-economic reference-data integration;
- aggregate, location/time/crime-type forecasting for preventive resource
  planning; and
- formal LLM input/output evaluation, including provider, prompt, safety, and
  evidence regression testing.

Live CCTNS/FIR database access or write-back remains unavailable until KSP
provides an approved source-system interface. V2 must therefore retain the
synthetic generator and landing-zone contract so that a future source producer
can be introduced without altering downstream pipeline logic.

**Individual future-criminality prediction must not be built.** A score that
claims a named accused is likely to commit a future crime would be an
unreliable, high-impact decision about an individual and conflicts with the
platform's evidence and accountability model. V2 will instead provide: (a) a
fully cited *recorded-history profile* for authorised investigators, (b) a
transparent *case-priority* indicator for work allocation, and (c) aggregated
place/time pattern forecasts. These outputs must not be relabelled as an
individual risk prediction.

---

## 3. V2 target architecture: Catalyst first, minimal rewrite

### 3.1 Target mapping

| Need | V2 technology | Existing compatibility seam | Do not use |
|---|---|---|---|
| API compute | Catalyst Advanced I/O Function running existing FastAPI app | Existing `cip_api` function and `create_app()` | Separate non-Catalyst API host |
| Scheduled/pipeline work | Catalyst Circuits + Event Functions | Existing `cip_refresh` stages and `SeedPipeline`/`Loader`/`DQ`/`Refresher` | External cron/SaaS scheduler |
| Web client | Catalyst AppSail / Web Client hosting | Existing AppSail console proxy | Vercel/Netlify/Firebase Hosting |
| Relational facts/intelligence | Catalyst Data Store + ZCQL | `DataStore` / `CatalystDataStore` | Firebase/hosted external DB |
| Session, scratch data | Catalyst NoSQL through new `KeyValueStore` adapter | Existing `KeyValueStore` / `MemoryService` | Redis/Firebase |
| Hot master/query cache | Catalyst Cache through a small cache adapter | Reference repository cache boundary | Redis/Memcached external service |
| Landing, exports, audio | Catalyst Stratus/File Store through `FileStore` adapter | Existing `FileStore` | S3/GCS/Firebase Storage |
| Authentication | Catalyst Authentication/OIDC bridge | New identity adapter/dependency only | Parallel username/password production auth |
| Kannada ASR/NMT/TTS | Bhashini through `LanguageService` | Existing Bhashini adapter | Browser-only as the sole supported path |
| LLM phrasing/routing | Existing `LLMGateway`, policy-approved provider only | Existing gateway interface | Direct provider calls from agents |

### 3.2 The integration principle

Every provider-specific capability must enter at one of four points:

1. An implementation of an existing port.
2. A new narrow port if no existing port describes the capability.
3. A container binding selected by settings.
4. A small API/router change that converts an HTTP request into a provider-
   independent application request.

Do not add Catalyst SDK calls inside `SupervisorAgent`, analytics, graph code,
or repositories that represent business queries. Doing so would bind core logic
to a runtime and force a rewrite for local tests.

### 3.3 Delivery order and gates

V2 is organized into gates. A later gate must not start until its predecessor
has automated verification and a rollback path.

```text
Gate 0: deployment inventory and contract tests
Gate 1: live Catalyst Data Store + Stratus
Gate 2: Catalyst Auth, NoSQL, Cache, and file ownership
Gate 3: Bhashini text, ASR, TTS, bilingual corpus
Gate 4: Catalyst Circuit pipeline and observability
Gate 5: analytics, sociology, and early-warning completion
Gate 6: retrieval/ER quality and agent evaluation
Gate 7: production security and pilot-readiness validation
```

Each gate is described below with implementation steps, compatibility strategy,
tests, and acceptance criteria.

---

## 4. Gate 0 — establish a safe V2 baseline

### Objective

Create a repeatable baseline before provider changes. This protects the current
working demo and gives every V2 change a measurable regression boundary.

### Implementation tasks

1. Create a dedicated Catalyst Development project and a separate Catalyst
   Production project/environment. Use synthetic data only in Development.
2. Record the exact Python, Node, Catalyst CLI, and dependency-lock versions.
3. Add a deployment configuration document with required setting names, but
   never commit credentials, refresh tokens, API keys, or signed URLs.
4. Run and record the existing unit/integration test suite against SQLite.
5. Add a `deployment-smoke` test collection that is skipped unless explicit
   Catalyst environment variables are present. It must never run against an
   unspecified project.
6. Inventory all currently local-only state: SQLite database, local filestore,
   relational KV, demo user accounts, reference cache, generated PDFs, audio
   response path, and seeded corpus.

### Compatibility approach

No domain, agent, graph, analytics, or UI behavior changes in this gate. This
is an environment and test baseline only.

### Acceptance criteria

- A clean local checkout still seeds and runs with zero credentials.
- The full existing test suite passes.
- A CI/deployment operator can identify exactly which variables select Catalyst
  and which project/environment is targeted.
- No secret appears in tracked files, test output, or PDF export.

---

## 5. Gate 1 — live Catalyst Data Store and Stratus

### Objective

Make the existing relational and file-store ports operate against a live
Catalyst project without changing agents, repositories, or pipeline semantics.

### 5.1 Provisioning and schema

1. Provision Catalyst Data Store tables from the repository schema. The source
   schema fields must remain unchanged; CIP tables retain `cip_`/`ext_` names.
2. Create the Stratus bucket declared as `cip-ingest` and establish prefixes:
   `landing/`, `raw/`, `manifests/`, `exports/{user_id}/`, and `audio/{user_id}/`.
3. Store the mapping of schema migration version to Catalyst deployment version
   in a control table. Catalyst deployment must fail before API rollout when
   the schema version is not compatible.
4. Do not call SQLite `executescript()` against Catalyst. Provision DDL using
   Catalyst-supported table management/IaC/CLI steps, then have the application
   verify the expected tables and columns during health/deployment checks.

### 5.2 Complete and harden `CatalystDataStore`

The existing adapter is the correct starting point. Extend it rather than
rewriting repositories.

1. Run its contract tests against an isolated Catalyst Development table.
2. Add a schema-capability check for every SQL pattern used by repositories.
   Unsupported ZCQL patterns must fail during deployment tests, not after an
   investigator sends a request.
3. Replace any SQLite-only repository statement with a small adapter-neutral
   repository method only where translation is impossible. Keep the same
   method signature and test it against both backends.
4. For read-then-write upserts, require a deterministic natural key and make
   retries safe. Persist a batch/job identifier before writes; record a
   completed marker only after all child rows have been written.
5. Keep query pagination in the adapter and assert that aggregation/retrieval
   does not silently truncate at the Catalyst response-page limit.
6. Add transient HTTP retry with bounded exponential backoff for idempotent
   reads and idempotent batch writes only. Do not blindly retry a non-idempotent
   mutation.
7. Log provider request IDs, operation class, duration, page count, and error
   category without logging sensitive row values.

### 5.3 Add `CatalystFileStore`

Implement `FileStore` in `infrastructure/catalyst/stratus.py` using Catalyst
Stratus/File Store APIs. It must provide the same methods as `LocalFileStore`:
`write_bytes`, `write_text`, `read_bytes`, `exists`, `list_keys`, and
`url_for`.

Rules:

- `url_for` returns a short-lived, signed URL or an authenticated application
  route; it must not make export files public.
- Application-level file authorization remains in the existing `/files/{path}`
  route. A signed URL is issued only after `authorize_file_access` succeeds.
- Use content type, content length, checksum, and object key in the audit
  record for exports and uploads.
- Keep all keys normalized; reject traversal-like keys before provider calls.

### 5.4 Container changes

Modify only the infrastructure binding in `build_container`:

- `_build_store(settings)` already selects Catalyst Data Store.
- Add `_build_filestore(settings)` selected by a new explicit setting such as
  `KSPCIP_FILESTORE_BACKEND=local|catalyst`.
- Local remains default for zero-credential development.
- Catalyst environments fail fast if `FILESTORE_BACKEND` is local, preventing
  accidental storage of case exports on the function filesystem.

### Tests and acceptance criteria

- Seed a small deterministic dataset to Catalyst Development.
- Confirm every curated/intelligence row count matches SQLite for the same
  seed, including graph edges, embeddings, alerts, and scores.
- Export a conversation PDF, download it through the authorized file route,
  verify another user is rejected, and inspect the Catalyst object/key.
- Re-run the same batch and prove no duplicate records are created.
- Force a DQ blocking failure and prove intelligence tables remain at the last
  known-good state.

---

## 6. Gate 2 — Catalyst Authentication, NoSQL, Cache, and secure session state

### Objective

Replace local production dependencies with Catalyst services while keeping the
existing `Principal`, `AuthorizationService`, `MemoryService`, and API agents
unchanged.

### 6.1 Catalyst Authentication integration

The existing demo account/password flow remains for local demos only. In
Catalyst environments:

1. Configure Catalyst Authentication as the browser sign-in authority.
2. Add a narrow `IdentityProvider`/token-verification adapter under
   `infrastructure/catalyst/`, or adapt `TokenService` behind its current
   boundary. It verifies Catalyst/OIDC-issued identity claims and maps them to
   a local `Principal`.
3. Maintain a `cip_user_account`/role mapping table in Catalyst Data Store for
   application roles, home unit, district, active status, and purpose/approval
   metadata. Catalyst identity identifies the person; KSP-CIP mapping decides
   police scope.
4. Put the mapping lookup in API dependency code, not individual agents.
5. Reject users with missing, disabled, or ambiguous scope. Never silently
   assign statewide access.
6. If Catalyst Authentication supports the chosen enterprise identity option,
   configure the approved OIDC/SAML federation and MFA/session policy there;
   do not maintain a parallel production password database.

Compatibility: routers continue to depend on `PrincipalDep`; all existing
agent calls and authorization tests retain `Principal` objects. Add a Catalyst
dependency implementation and preserve the current local demo dependency for
tests.

### 6.2 Catalyst NoSQL for session/state

Implement `CatalystKeyValueStore` under `infrastructure/catalyst/` using
Catalyst NoSQL. Bind it to the existing `KeyValueStore` port.

Recommended namespaces:

- `session_state`: language, pins, last intent, expiry;
- `agent_scratchpad`: explicitly non-authoritative per-request temporary
  material, TTL one hour or less;
- `embedding_cache`: query-vector/cache entries, TTL one day;
- optionally `idempotency`: short-lived request/batch deduplication keys.

`MemoryService` needs no logic rewrite. It already stores a small JSON document
by namespace/key and uses TTL. Conversation transcripts may remain in the
relational `cip_conversation_turn` table because ordered export/audit queries
are relational; session pins move to NoSQL.

Requirements:

- Namespace keys include the user identifier where a session identifier is not
  globally guaranteed unique.
- TTL is set at write time and expiry is enforced by provider plus the existing
  purge/retention job.
- Do not put full FIR content, raw audio, or unbounded chat transcripts into
  scratchpad/cache documents.
- Treat cache loss as normal: persistent repositories must remain authoritative.

### 6.3 Catalyst Cache

Use Catalyst Cache only for replaceable data: master lookups, reference labels,
health/report aggregates, short-lived query embeddings, and optionally TTS
bytes by text hash + language. Do not use cache as the source of truth for
authorizations, audit events, or evidence.

Create a small `CacheStore` port only if `KeyValueStore` does not accurately
express Catalyst Cache semantics. The reference repository’s existing
in-process cache is retained for local operation; bind a Catalyst cache-backed
implementation in Catalyst environments and add explicit invalidation after a
successful load/refresh.

### 6.4 File and export ownership

Preserve current export key structure: `exports/{principal.user_id}/...`.
Neither an analyst nor an administrator should receive a blanket object-store
URL that bypasses application policy. Use the existing file-access checks, with
auditor exceptions only where already authorized by policy.

### Tests and acceptance criteria

- An Investigator can query only their unit subtree after Catalyst login.
- A Policymaker receives aggregates only and cannot retrieve named case detail.
- An Auditor can inspect audit events but cannot gain case access merely by
  being an auditor.
- Session coreference works across function invocations, expires after TTL,
  and cannot be read by another user.
- Cache invalidation after pipeline refresh returns current master/graph data.

---

## 7. Gate 3 — full Kannada and voice through Bhashini

### Objective

Make language and voice capability real, observable, and safe while retaining
the offline glossary/browser route as an explicitly labelled fallback.

### 7.1 Configure the existing adapter

The existing `BhashiniLanguageService` already implements translation, ASR,
and TTS under `LanguageService`. V2 should:

1. Obtain approved Bhashini credentials and pipeline/model identifiers.
2. Store secrets in Catalyst configuration/secrets facilities, not in `.env`,
   code, browser storage, or exported logs.
3. Set `KSPCIP_LANGUAGE_PROVIDER=bhashini` only in approved Catalyst
   environments.
4. Add startup readiness checks that report Bhashini as ready/degraded without
   exposing credentials.
5. Preserve `LocalLexiconLanguageService` as a fallback; surface
   `language_full_fidelity=false` whenever fallback is active.

### 7.2 Bilingual corpus generation

Extend the synthetic pipeline, not the chat agent:

1. Generate English deterministic `BriefFacts` exactly as now.
2. Add a translation stage after data generation and before embedding refresh.
3. Translate only approved synthetic text batches through Bhashini. Store the
   Kannada result in existing `cip_brief_facts_kn`, record source language,
   provider/model/pipeline identifier, timestamp, source hash, and translation
   status in a CIP control/metadata table.
4. Cache translations by source-text hash in Catalyst Cache/NoSQL to avoid
   repeated calls during replay.
5. A failed translation must not block curated FIR loading. It marks the
   Kannada variant unavailable and allows English retrieval, while health/DQ
   reports the deficit.
6. Refresh embeddings after translation so English and Kannada text can be
   retrieved through the same case identifier.

This keeps facts in the curated record and language variants as clearly
identified enrichment, rather than inventing a second case source.

### 7.3 Server-side ASR endpoint

The existing chat router already has a server-side transcription path. Complete
it as follows:

1. Add explicit request limits: MIME allow-list, maximum bytes/duration,
   required declared/auto-detected language, and per-user rate limit.
2. Receive audio through the API function; do not persist by default.
3. Pass bytes only to `LanguageService.transcribe`.
4. Return transcript, detected language, provider status, and a correlation ID.
5. Audit only metadata/hashes and outcome—not raw voice content or transcript
   unless the approved audit policy requires it.
6. If ASR fails, return a precise problem response and let the UI retain the
   typed-input path. Do not substitute a guessed transcript.

The browser Web Speech API remains an optional convenience, not the sole
supported voice implementation.

### 7.4 TTS response and UI integration

Add a small optional `audio` field to the existing answer response schema:

1. A client sends `want_audio=true` on a chat request.
2. After evidence composition and language translation, call
   `LanguageService.synthesize(answer_text_display, language)`.
3. If bytes are returned, write them through `FileStore` to
   `audio/{user_id}/{session_id}/{turn-or-content-hash}.mp3` (or provider
   format) with short retention.
4. Return an application-authorized audio URL, mime type, expiry/availability
   state, and no claim that audio exists if synthesis failed.
5. The React `ChatPanel` renders a standard play/pause control only when an
   authorized audio URL is present.
6. Cache by `sha256(display text + language + voice settings)` in Catalyst
   Cache where permitted. Audio cache entries must never cross users if the
   policy treats responses as sensitive.

### Tests and acceptance criteria

- Kannada text query produces a correct normalized English working query and a
  Kannada display answer for representative FIR/trend/network prompts.
- English and Kannada case text retrieve the same authorized case IDs.
- ASR is tested with approved synthetic/consented clips for English and Kannada.
- TTS audio plays only for the requesting authorized user and expires/purges as
  configured.
- Bhashini outage produces a visible degraded state and safe text fallback.

---

## 8. Gate 4 — Catalyst Circuits, scheduled refresh, and operations

### Objective

Operate the existing pipeline reliably on Catalyst rather than treating the
local seed command as production orchestration.

### 8.1 Circuit design

Use Catalyst Circuits and the existing `cip_refresh` event function. Retain the
current stages and make them explicit/idempotent:

```text
receive/land batch -> validate descriptor -> load -> DQ -> gate
  -> identity/graph/embedding/analytics refresh -> cache invalidation
  -> retention -> run report
```

The current nightly Circuit is a good skeleton. V2 should add:

- an on-demand admin seed/refresh path that creates a tracked job rather than
  doing long work inside a synchronous user request;
- separate schedule configuration for nightly ingest/DQ/intelligence and a
  weekly full recomputation if cost/latency requires it;
- retry only at stage boundaries with a persisted idempotency key;
- terminal failure status, operator alert, and a queryable run report;
- explicit cache invalidation only after a successful intelligence refresh;
- no automatic deletion of audit events during retention.

### 8.2 Control-plane records

Enhance the existing control tables rather than add a second scheduler DB.
Every run should store:

- run ID, stage, Catalyst execution ID, trigger type, attempt number;
- batch IDs/input object keys and checksums;
- start/end timestamps, row counts, DQ counts, refresh counts;
- prior/current data watermark and published intelligence version;
- error code/message category and correlation ID.

The UI admin panel can then show truthful data freshness and job status instead
of merely showing that a function was invoked.

### 8.3 Observability

Use Catalyst logging/monitoring plus structured application logs. Add metrics
as structured events if direct metric integration is unavailable:

- API rate, errors, p50/p95 duration;
- response type and evidence count;
- pipeline lag, batch age, DQ failure count;
- graph size, identity review queue size, embedding refresh duration;
- Bhashini/LLM provider latency and failure rate;
- Catalyst Data Store query page count and failure category.

Never log raw FIR text, passwords, bearer tokens, refresh tokens, audio bytes,
or full sensitive demographics. Correlation IDs link logs to audit events.

### Tests and acceptance criteria

- A failure in DQ stops downstream intelligence refresh.
- A retry resumes safely without duplicate seeded/loaded records.
- A run report identifies the exact stage and batch that failed.
- Health and UI display data freshness based on watermarks, not current clock.
- Triggering the nightly and on-demand paths yields the same derived state for
  the same input.

---

## 9. Gate 5 — complete analytics and sociology without unsafe claims

### 9.1 Seasonal analysis

Add a new deterministic seasonal method to `AnalyticsEngine`; do not prompt an
LLM to infer seasonality.

Implementation:

1. Add `seasonality(filters, scope, comparison_years, grouping)` returning a
   typed result with month/week buckets, current-period counts, historical
   baseline, variance, observed deviation, source case IDs, and a
   `ComputationTrace`.
2. Add repository aggregates grouped by calendar month and filtered by crime,
   district/unit, and date. Scope is part of every query.
3. Define a minimum-history rule. When fewer than two comparable annual periods
   exist, return “insufficient history” with evidence rather than an apparent
   seasonal finding.
4. Route an explicit seasonal intent in NLU and `CrimeAnalyticsAgent`.
5. Add a chart payload (`line`/`bar`) and UI explanation: “recorded FIR counts,
   not population-adjusted crime rate.”
6. Include a synthetic planted seasonal signal and integration test that proves
   the engine detects it without making non-planted periods look significant.

Compatibility: this is a new method/result/intent. Existing trend/hotspot APIs
and tables remain unchanged.

### 9.2 Event-based analysis

Do not treat a calendar coincidence as causation. Introduce it only with an
approved, aggregate event dataset.

1. Add `cip_event_calendar` as a CIP-derived/reference table: event ID, name,
   type, date range, geography/unit scope, source, data-quality status, and
   approval status.
2. Build a restricted admin ingestion path using Catalyst landing files and the
   same DQ/control mechanism.
3. Add `event_comparison` in `AnalyticsEngine`: compare event-window counts to
   matched non-event windows and historical same-period baselines. Return
   sample size, comparison window, confidence caveat, and case evidence.
4. UI wording must say “coincides with” or “was elevated during,” never “was
   caused by,” unless an approved causal study methodology exists.
5. Give Policymakers aggregate-only output and do not expose sensitive case
   lists in event charts.

### 9.3 Sociological insight completion

The current sociology method is intentionally narrow. Extend in stages:

1. Add a controlled subject selector: `complainant` or `victim`. Keep accused
   sensitive-demographic analytics disabled until legal/policy approval.
2. Make dimension allow-lists explicit by role. Age band, gender, and
   occupation may have different rules from caste/religion.
3. Add small-cell suppression. If an aggregate group is below an approved
   threshold (for example, fewer than 10 records), suppress/merge it to avoid
   re-identification. The threshold must be configurable and audited.
4. Provide population-denominator status. Without denominator data, label every
   result as “share of recorded complaints/victims,” not a population crime rate.
5. For external indicators (urbanization, migration, education, economic
   stress), first obtain approved source datasets with geographic/time coverage,
   update history, licensing, and governance owner. Land them in Catalyst
   through a separate `ext_social_indicator` reference layer; do not mix them
   into FIR source tables.
6. Build descriptive joins at district/year level with missing-data reporting.
   Report correlation coefficient, sample size, time window, data-source
   version, and the statement “correlation does not establish causation.”
7. Require a human analytics/legal review before any public/policy interpretation
   is enabled.

### 9.4 Early warning versus forecasting

Keep existing anomaly detection as the first production capability. It is
explainable: trailing observed count versus each district/crime type’s own
historical baseline with a z-score and floor.

V2 requires a controlled place-level forecasting workstream:

1. Start with an evaluated seasonal-naive baseline, not a black-box model.
2. Forecast aggregates by area/week/crime type, never individual propensity.
3. Train/re-score in a Catalyst scheduled pipeline using versioned datasets and
   store model metadata/artifacts in Stratus plus model records in Data Store.
4. Compare every candidate against baseline on a held-out time period using
   MAE/RMSE/calibration and geographic fairness checks.
5. Produce a model card: intended use, prohibited use, input period, features,
   performance, limitations, approval, and rollback version.
6. Surface forecasts as advisory “resource-planning signals,” with uncertainty
   bands, not deterministic crime predictions.

Do not introduce person-level future-risk prediction. It conflicts with the
safety posture; the recorded-history and case-priority outputs described above
are the approved individual/case-level alternatives.

### 9.5 Real financial-data ingestion and investigation workflow

The current `ext_financial_transaction` dataset is a correctly labelled
synthetic extension. V2 must preserve that label until an approved real-data
contract exists; it must not overwrite or masquerade as a source FIR table.

#### Data and governance contract first

Before implementation, obtain written approval from the financial-investigation
owner and legal/privacy stakeholders for:

1. Allowed source systems and the approved delivery mechanism. Prefer an
   authorised, KSP-controlled export/secure delivery into Catalyst Stratus;
   do not add a direct connection to a bank, payment rail, or an unapproved
   cloud account.
2. Exact fields, identifiers, classification, retention, permitted roles,
   masking rules, audit requirements, reconciliation owner, and error-correction
   procedure.
3. Entity-resolution rules for account, UPI handle, phone, merchant, wallet,
   and counterparties. A possible association is a reviewable link, not proof
   of ownership or guilt.
4. Authority for transaction amounts, account references, and counterparty data
   to appear in case records, graph payloads, PDF exports, or LLM/language
   requests. Default to no external-provider exposure.

#### Catalyst-first ingestion design

1. Land encrypted/approved batches in a restricted Catalyst Stratus prefix,
   such as `financial-landing/{provider}/{batch_id}.ndjson`. The delivery
   producer must include batch ID, extraction time, row count, checksum, and
   source-system version.
2. Extend the existing loader with a dedicated financial descriptor and DQ
   suite. Reuse `ctl_batch`, `ctl_dq_result`, watermarks, idempotency, and
   Circuit stages rather than creating a second pipeline.
3. Add a separate CIP table family such as `ext_financial_transaction`,
   `ext_financial_entity`, and `cip_financial_entity_link`. Keep source IDs,
   hashes, link method, confidence/decision, provenance, and source batch ID.
   Store sensitive account identifiers in a masked/tokenized form where the
   analytics requirement permits; expose full values only under an explicit
   permission and audit event.
4. Add Data Store uniqueness constraints/natural-key checks based on the source
   transaction identifier plus source system. A replayed batch must not create
   duplicate money-flow edges.
5. Run a Circuit stage after financial DQ to build `MONEY_FLOW` graph edges and
   refresh financial summaries. The graph builder receives the same scope model
   as case edges.
6. Add permissions such as `READ_FINANCIAL_DETAIL` and `USE_FINANCIAL_TOOLS`.
   The existing financial-tool permission is the starting point; V2 separates
   aggregate/link access from unmasked transaction-detail access if policy
   requires it.

#### Analyst workflow and explanation

The Financial Link Agent should return a clearly structured result:

- source transaction/provenance locators and source batch freshness;
- direct and multi-hop flow paths, with each edge labelled source-recorded or
  inferred association;
- amount/date/count summaries plus filtering logic;
- a conflict/missing-data notice where a source account/entity cannot be
  confirmed; and
- an exportable, role-gated money-trail report watermarked with the requester.

The UI must distinguish *recorded transfer*, *resolved entity*, and *inferred
association* visually. It must not say that a flow proves criminal ownership,
intent, or guilt.

#### Financial acceptance tests

- An approved synthetic fixture and, later, a controlled non-production sample
  prove batch reconciliation, deduplication, DQ, scope enforcement, masking,
  and evidence locators.
- A user without financial permission cannot retrieve transaction values via
  chat, REST, graph payload, citation, export, cache, or error text.
- Replaying the same source batch leaves transaction/edge counts unchanged.
- A graph path shows every underlying transaction locator and accurately
  preserves synthetic versus real-source provenance.

---

## 10. Gate 6 — multilingual retrieval, entity quality, and agent evaluation

### 10.1 Improve semantic retrieval without rewriting RAG

The current retrieval service depends on `EmbeddingModel`, which is the correct
swap boundary. V2 should add a multilingual adapter rather than change the
retrieval agent’s behavior.

Recommended staged approach:

1. Retain `HashedNgramEmbeddingModel` as the offline/local fallback and test
   oracle.
2. Add `BgeM3EmbeddingModel` (or another approved multilingual model) behind
   `EmbeddingModel`. Host/execute it only in an approved Catalyst-compatible
   runtime plan; if Catalyst cannot host the model at required scale, do not
   send unredacted FIR text to an unapproved external embedding service.
3. Store `model_name`, dimensions, source hash, language, and indexed-at time
   for each `cip_embedding_index` row.
4. Introduce dual-index rollout: write new embeddings alongside old model
   versions; compare retrieval results in shadow mode; switch the active model
   by setting only after evaluation passes; later purge obsolete versions.
5. Chunk free text deterministically with source table/primary key/offset
   provenance. A result must always map back to CaseMasterID/CrimeNo.
6. Preserve ACL pre-filtering before ranking. A semantically good result outside
   the caller’s scope must never reach the ranker or model prompt.

### 10.2 Entity-resolution calibration

The existing link-not-merge architecture is right. Improve quality without
changing it:

1. Build a synthetic labelled pair suite first, then an approved analyst-labelled
   review sample when real data governance permits.
2. Measure precision, recall, false-link rate, and review-queue rate at every
   threshold by district, language/transliteration pattern, age availability,
   and record vintage.
3. Preserve the current decision bands: auto-link, review, reject. Change
   threshold values only through configuration with recorded evaluation results.
4. Extend explainable feature evidence—normalised names, token/phonetic score,
   age compatibility, geography compatibility—without replacing the source
   rows or silently merging identities.
5. Require an explicit reviewer decision with actor/timestamp/reason for
   non-automatic links. Decisions trigger graph/score refresh via Circuit.
6. Test that a rejected link cannot reappear merely due to cache refresh or
   pipeline retry.

### 10.3 Agent evaluation harness

The most important V2 quality control is an evaluation suite that tests the
whole agent system as a controlled pipeline, not only individual functions.

Create versioned fixtures with:

- questions by intent in English and Kannada;
- expected intent/slots and authorized scope;
- expected case/aggregate/edge locators;
- expected structured payload type;
- expected refusal or permission denial where applicable;
- adversarial prompts requesting unsupported facts, scope bypasses, or
  individual predictive claims;
- seeded-signal tests for hotspots, surge, communities, transliteration, and
  financial synthetic markers.

For each fixture assert:

1. NLU chooses the expected intent or safe clarification.
2. Agent routing uses only the allowed specialist agents.
3. Every numeric/inferred/synthetic statement has the expected evidence class.
4. No record outside scope appears in answer text, citations, payload, trace,
   or memory pins.
5. The LLM-on and LLM-off paths preserve factual locators and numbers.
6. A Bhashini/local language provider changes display fluency, not access,
   evidence, calculations, or routing policy.

Track evaluation results in Catalyst Data Store/Stratus for deployed versions;
never use live user conversation logs as ungoverned model-training data.

### 10.4 LLM input/output evaluation and release gate

The LLM is deliberately constrained, but it still needs formal evaluation.
Testing cannot be limited to “the response sounded good.” V2 must prove that
provider/model/prompt changes do not alter facts, weaken citations, leak data,
or break routing.

#### What is being tested

The test surface is every call through `LLMGateway`:

- optional intent disambiguation when deterministic NLU confidence is low;
- optional prose polishing after deterministic claims/evidence are composed;
- provider failure, timeout, malformed output, quota exhaustion, and fallback;
- multilingual display behavior after language conversion; and
- provider/model/prompt-template/configuration upgrades.

The LLM is not tested as a source of truth, because it is not allowed to be
one. The deterministic agent result is the factual oracle against which LLM
output is judged.

#### Versioned test corpus

Create `tests/evals/` with versioned JSON/NDJSON fixtures and a manifest. Each
fixture contains:

```json
{
  "id": "trend-mysuru-authorized-001",
  "principal_fixture": "analyst_state",
  "input": {"language": "en", "text": "What is the theft trend in Mysuru?"},
  "expected": {
    "intent": "trend_query",
    "required_locators": ["AGG:"],
    "forbidden_case_ids": [],
    "payload_type": "chart",
    "must_refuse": false
  },
  "risk_tags": ["analytics", "scope", "numeric_claim"]
}
```

The corpus must include:

1. Every supported intent, slot type, role, and structured payload.
2. English, Kannada, code-mixed, incomplete, typo-heavy, and ambiguous input.
3. Multi-turn coreference with expected pinned case/person/district entities.
4. Out-of-scope requests, privilege-escalation attempts, prompt injection,
   requests for hidden instructions, and requests to invent citations.
5. Requests for sensitive demographics, real financial values without
   permission, unapproved causal claims, and individual future-criminality
   predictions; these must be refused or redirected to the approved output.
6. Citation-preservation cases containing dates, amounts, numeric counts,
   inferred links, empty-result claims, and synthetic financial markers.
7. Provider outage/timeout/malformed-response fixtures that prove deterministic
   fallback or safe error behavior.

Use the seeded manifest so important known signals have exact expected answers.
Never put unapproved real FIR content, credentials, raw voice recordings, or
unredacted personal data in the evaluation corpus.

#### Three-level evaluation design

**Level A — deterministic unit/contract tests.** Test `LLMGateway` provider
adapters with recorded, sanitized provider responses. Assert request schema,
timeout, retry/fallback behavior, token accounting, and response parsing. These
tests run offline and do not spend provider quota.

**Level B — end-to-end factual regression.** Run each corpus prompt through the
same local seeded database twice: once with the local deterministic LLM/no
polish path and once with the candidate provider/model/prompt. Compare:

- intent and permitted specialist routing;
- resolved slots/coreference;
- case IDs, crime numbers, aggregate values, alert IDs, and graph edge IDs;
- evidence locator set and provenance markers;
- payload type/data and computation trace;
- scope/masking behavior; and
- refusal/clarification outcome.

The provider path may improve wording, but it may not add or remove a fact,
number, source locator, permission decision, or safety label. The existing
`verify_rewrite`/evidence enforcement is the runtime guard; this evaluation
proves it continues to hold across a release set.

**Level C — live provider acceptance.** In an isolated Catalyst Development
environment, invoke the approved LLM provider through the real gateway with
only synthetic/redacted fixtures. Record provider name, model, prompt-template
version, response hash, latency, token use, pass/fail checks, and correlation
ID in a Catalyst Data Store evaluation table. Store any necessary detailed
artifacts in a restricted Stratus prefix with retention and access rules.

#### Quantitative release criteria

Set and enforce a release manifest. A candidate provider/model/prompt cannot be
promoted unless all mandatory safety tests pass and the following are met on the
approved corpus:

- 100% no out-of-scope record/citation/payload leakage;
- 100% preservation of required inferred/synthetic/empty-result markers;
- 100% refusal/redirection of prohibited individual-risk requests;
- 100% citation/evidence validator pass for shipped answers;
- 100% safe fallback or explicit failure on injected provider failures;
- intent/slot accuracy at or above a documented baseline for unambiguous test
  cases; and
- no material regression in latency/token budget beyond an approved threshold.

Language fluency can be assessed by approved bilingual reviewers using a small
rubric: semantic adequacy, police-domain terminology, readability, and whether
the display text changes the meaning/evidence status. Reviewers score language,
not truth; factual truth remains a deterministic comparison.

#### Prompt/provider change control

1. Version every prompt file, provider name/model, temperature, token limit,
   language pipeline, and embedding model in source/configuration.
2. A change opens an evaluation run against the fixed corpus in Catalyst
   Development.
3. The run produces a diff report: changed answers, locator differences,
   latency/cost changes, failures, and reviewer language scores.
4. A designated technical and policy approver signs off before Production
   promotion. Failed candidates are rolled back by settings to the previous
   provider/model/prompt version; agents/repositories are unchanged.
5. Production monitoring samples metadata and safety-validator failures, not
   raw sensitive prompts by default. Any production review uses the approved
   audit/privacy process.

---

## 11. Gate 7 — security, governance, and pilot readiness

### 11.1 Security controls to implement/verify

1. Use Catalyst Authentication and an approved MFA/SSO policy for non-demo
   environments.
2. Store provider credentials in Catalyst secret/configuration facilities and
   rotate them. Remove development defaults from Production configuration.
3. Set restrictive CORS origins for deployed UI domains only.
4. Add API Gateway/function-level rate limits for login, ASR, chat, exports,
   and admin pipeline operations. Rate limits supplement, never replace, role
   authorization.
5. Preserve parameterized repository calls and test Catalyst ZCQL escaping with
   malicious inputs, Unicode names, and quote/backslash cases.
6. Require explicit maximum upload size/type for voice and batch inputs.
7. Verify audit retention/configuration, export watermarking, and authorization
   checks with live Catalyst storage.
8. Run dependency vulnerability checks, secret scanning, static analysis, and
   focused dynamic API tests in the approved CI/deployment process.
9. Document an incident response path: disable user, revoke provider credential,
   pause Circuit, preserve audit, and communicate to the approved KSP owner.

### 11.2 Privacy and use governance

1. Create a data-minimization matrix listing every agent/service, columns it
   reads, purpose, role permission, retention, and external exposure.
2. Ensure language/LLM requests receive the minimum text required. Prefer
   deterministic templates over provider calls for sensitive answers.
3. Do not send caste/religion/employee-sensitive fields to embeddings, LLMs,
   or language services unless an approved exception explicitly requires it.
4. Maintain aggregate-only safeguards, small-cell suppression, and clear
   labels for inference/synthetic data.
5. Obtain written legal/department approval before connecting real FIR or
   financial data, deploying external model providers, or enabling forecasts.

### 11.3 Reliability and recovery

1. Define Catalyst backup/export/rebuild procedures for curated and control
   tables, Stratus landing objects, and code/configuration.
2. Test restoration to an isolated Catalyst environment using synthetic data.
3. Make derived graph/embeddings/alerts/scores rebuildable from curated records
   and landing files; they must not be the only copy of intelligence inputs.
4. Document realistic RPO/RTO and perform a restore drill before a pilot.
5. Use an environment separation strategy: Development synthetic data,
   staging masked/approved data if applicable, and production only after
   governance approval.

---

## 12. File-by-file minimal-change roadmap

| Area | Add/change | Preserve |
|---|---|---|
| `config/settings.py` | Catalyst NoSQL/Cache/File Store/Auth settings; explicit backend selectors; production fail-fast validation | Existing local defaults and all current settings names |
| `domain/ports/__init__.py` | Add only narrow ports needed for Catalyst auth/cache; retain existing DataStore/FileStore/KV/Language/LLM/Embedding ports | Domain remains provider-agnostic |
| `infrastructure/catalyst/` | `stratus.py`, `nosql.py`, `cache.py`, `identity.py`, deployment health helper | `datastore.py` as the relational adapter |
| `interface/container.py` | Provider factories/bindings selected by settings | All agents, repositories, and service construction |
| `interface/api/deps.py` | Catalyst principal dependency and local-demo fallback | Router contract receiving `Principal` |
| `interface/api/schemas.py`, `routers/chat.py` | Optional audio request/response metadata, bounded ASR | Existing text chat response and evidence model |
| `application/services/memory.py` | No material rewrite; optionally add user-qualified key helper | Current coreference and transcript logic |
| `application/services/pdf_export.py` | Use injected Catalyst FileStore transparently | Watermark, evidence notice, owner path behavior |
| `application/pipeline/` | Translation job, control-run metadata, cache invalidation event | Generator/loader/DQ/refresher ordering |
| `application/analytics/engine.py` | New seasonal/event methods and typed results | Trend/hotspot/early-warning arithmetic |
| `application/agents/` | Add new explicit intents/routes only; use existing evidence composer | The five-agent boundary and deterministic enforcement |
| `infrastructure/embeddings/` | New multilingual model adapter, dual-index support | Retrieval service and ACL-first ranking |
| `frontend/src/` | Language status, ASR upload option, TTS player, seasonal/event charts, job/freshness status | Existing evidence chips, graph, timeline, auth/session flow |
| `catalyst/` | Real deployment descriptors, Circuit stages, environment configuration docs | One-codebase deployment model |
| `tests/` | Backend contract, live-Catalyst opt-in, evaluation fixtures, security/regression tests | Current fast deterministic local suite |

No V2 task should directly modify the raw organiser-schema columns to make a
feature easier. Add CIP control/enrichment/reference tables or adapters instead.

---

## 13. Final V2 acceptance checklist

V2 is complete only when all relevant boxes are demonstrably true:

- [ ] Catalyst Development deployment runs the same API/UI behavior as local.
- [ ] Catalyst Data Store and Stratus are live-tested, including idempotent
      pipeline replay and export authorization.
- [ ] Catalyst Authentication maps users to existing `Principal`/scope rules.
- [ ] Catalyst NoSQL holds session state and Catalyst Cache holds only
      replaceable cached data.
- [ ] Bhashini EN/KN translation, ASR, and TTS are tested; fallback state is
      visible and truthful.
- [ ] Kannada text is generated/indexed for the synthetic corpus, with
      provenance and failure handling.
- [ ] Circuit jobs expose run status, DQ gate result, freshness, and retry-safe
      behavior.
- [ ] Seasonal analysis and, if approved, event comparison have deterministic
      computations, evidence, and caveats.
- [ ] Sociology has role controls, small-cell suppression, denominator labels,
      no causal overclaiming, and governed external-indicator provenance.
- [ ] Approved external social-indicator data is landed, versioned, quality
      checked, scope-limited, and reported only with correlation caveats.
- [ ] Early warning is clearly distinguished from evaluated aggregate
      place/time/crime-type forecasting; forecasts include uncertainty, model
      version, baseline comparison, and approved-use limitations.
- [ ] Financial-source ingestion, if approved, uses Catalyst Stratus/Data
      Store/Circuits, has reconciliation/DQ/masking/permission tests, and
      preserves real-versus-synthetic provenance on every flow.
- [ ] Entity-resolution thresholds are evaluated and review decisions are
      durable/replay-safe.
- [ ] Agent evaluation fixtures prove routing, evidence, scope, language, and
      LLM-safe behavior.
- [ ] LLM provider/model/prompt changes pass the versioned input/output corpus,
      factual-diff checks, failure tests, and bilingual review gate before
      promotion.
- [ ] Security, secret handling, rate limits, auditing, retention, and restore
      procedures are verified for the Catalyst environment.
- [ ] No Firebase or other parallel cloud backend/storage/auth service has
      been introduced.

## Closing principle

V2 should make the platform more real without making it less trustworthy. The
current code already contains the essential discipline: deterministic facts,
evidence before prose, query-level scope, reversible identity links, and
explicit limits. The correct path is not a rewrite. It is to complete the
Catalyst adapters and deployment validation, strengthen language and quality
evaluation at their existing boundaries, and add only deterministic,
evidence-bearing analytical features.
