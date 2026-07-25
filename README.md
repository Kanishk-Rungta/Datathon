# KSP-CIP — Crime Intelligence Platform

Conversational crime intelligence for the Karnataka State Police, built over
the FIR schema. An officer asks a question in English or Kannada; the platform
answers with **every factual statement bound to the records it rests on**.

> All data in this build is synthetic. Nothing here is a real case, a real
> person, or a real financial record. Outputs are intelligence products for
> investigative use — not evidence.

---

## Quick start

**Requirements:** Python 3.11+ and (optionally) Node 18+ for the web console.

```bash
cd ksp-cip
./scripts/setup.sh        # creates .venv, installs deps, builds the console
./scripts/seed.sh         # generates ~4,200 synthetic FIRs (~30 seconds)
./scripts/run.sh          # http://127.0.0.1:8000
```

Then open **http://127.0.0.1:8000** and sign in.

On Windows, use WSL or Git Bash — or run the three CLI commands directly:
`python -m venv .venv`, then `.venv\Scripts\pip install -r` the packages listed
in `scripts/setup.sh`, then `cd backend && python -m ksp_cip.cli seed` and
`python -m ksp_cip.cli serve`.

No API keys. No network. No GPU. The platform ships with a deterministic
offline LLM provider and an offline Kannada glossary, so it runs and its tests
pass with zero credentials.

Sign in with any account listed on the login screen — password `ChangeMe#2026`.

| Account | Role | Sees |
|---|---|---|
| `io.bengaluru` | Investigator | one police station's subtree |
| `analyst.state` | Analyst | statewide, no financial tools |
| `sp.mysuru` | Supervisor | district, plus the identity review queue |
| `policy.home` | Policymaker | aggregates only — never a named individual |
| `auditor.internal` | Auditor | the audit trail |
| `admin.platform` | Platform admin | pipeline and data quality |

Ask the same question as two different roles. The answers differ, and the
platform says why.

---

## What it does

**Ask** — "Where are the hotspots?", "What's the trend in Mysuru this year?",
"Who are the repeat offenders?", "How is X connected to Y?", "Brief me on FIR
104430006202600001", "ಮೈಸೂರು ಜಿಲ್ಲೆಯಲ್ಲಿ ಕಳ್ಳತನ ಪ್ರಕರಣಗಳು".

**Get back** — an answer where each sentence carries a citation chip you can
click to open the source FIR, a chart or link diagram, and a "how this was
computed" panel showing the query, the parameters and the formula.

Capabilities: FIR retrieval and semantic similar-case search · trend analysis ·
geographic hotspots · statistical early warning · sociological breakdowns ·
link analysis with entity resolution · recorded-offence-history scoring ·
money-flow analysis (synthetic extension) · case briefings with timelines and
a transparent Investigation Priority Indicator · PDF export · full audit trail.

---

## What makes it trustworthy

These are enforced by code and tests, not by prompt wording.

**The language model never knows anything.** It routes when rules are unsure
and it phrases already-computed answers. Every figure is arithmetic in
`application/analytics`. A polished answer is re-verified: citations must
match exactly, no new number may appear, provenance markers must survive — or
the rewrite is discarded.

**No claim ships without evidence.** A numeric or inferred claim with no
verifiable locator raises an error and the answer never leaves the composer.
Even "nothing found" carries a locator, because that is still an assertion
about a specific query.

**Inference is visible.** Derived links render as `(inferred)`. Synthetic
financial data renders as `(synthetic extension)`. In the link diagram,
inferred edges are dashed — always.

**Authorization is in the query.** A caller's unit subtree is injected into the
SQL `WHERE` clause and into graph traversal. Records outside it are never
retrieved, so they cannot leak through a snippet. When a link view is trimmed,
the platform says how many links it withheld.

**Identity is never silently merged.** Name matching auto-links above 0.90,
queues 0.72–0.90 for human review, and keeps every source row. Nothing is
irreversible.

**Scores describe records, not people.** The offender score summarises
recorded history — case count, offence variety, recency, gravity escalation,
network position — with every weight published. It is not a prediction and is
labelled as such.

---

## Architecture

```
React console  ──►  FastAPI  ──►  SupervisorAgent
                                      │
              ┌───────────────────────┼───────────────────────┐
              ▼                       ▼                       ▼
      DataRetrieval          CrimeAnalytics          NetworkIntelligence
        (SQL + RAG)        (trends, hotspots,      (graph, entity resolution,
                            early warning)          offenders, money flow)
              └───────────────────────┬───────────────────────┘
                                      ▼
                            InvestigationSupport
                       (briefings, timelines, priority)

Deterministic services (not agents): authorization · audit · evidence ·
memory · language · identity · PDF export
```

Five agents, exactly. Everything that decides who may see what, what counts as
evidence, or what goes in the audit log is ordinary code with ordinary tests.
See [ADR-0002](docs/adr/0002-five-agent-architecture.md).

Data flows raw → curated → intelligence, with hash-diff change detection, a
data-quality gate that halts the pipeline on a blocking failure, and an
append-only audit trail.

---

## Documentation

| Document | Contents |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Engineering memory — layout, status, gotchas, how to resume |
| [ADR-0001](docs/adr/0001-hackathon-production-reconciliation.md) | Production architecture vs hackathon constraints |
| [ADR-0002](docs/adr/0002-five-agent-architecture.md) | Five agents; deterministic services |
| [ADR-0003](docs/adr/0003-llm-is-not-a-source-of-truth.md) | How the LLM is contained |
| [ADR-0004](docs/adr/0004-persistence-ports-and-adapters.md) | Ports, adapters, SQLite/Catalyst |
| [ADR-0005](docs/adr/0005-synthetic-financial-extension.md) | Financial data as a marked extension |
| [ADR-0006](docs/adr/0006-analytics-are-computed-not-prompted.md) | Every figure is arithmetic |

API documentation is served at `/api/v1/docs`.

---

## Tests

```bash
scripts/test.sh                  # 201 tests
scripts/test.sh -m "not slow"    # unit tests only, seconds
```

The generator plants known signals — a surge, three hotspots, a network ring
with transliteration variants — and records them in a manifest. Integration
tests assert the analytics find them again. Without that loop, analytics over
synthetic data would prove nothing.

---

## Deploying to Catalyst

`catalyst/` maps every local module to a deployment component: the FastAPI app
becomes an AdvancedIO function, the pipeline stages become a scheduled Circuit,
the console becomes an AppSail app. Switching persistence is one variable:

```bash
export KSPCIP_DATASTORE_BACKEND=catalyst
```

The Catalyst adapter's ZCQL translation and escaping are covered by contract
tests. It has **not** been run against a live Catalyst project in this build.

---

## Honest limitations

- Kannada is an offline glossary unless Bhashini credentials are configured.
  The platform reports this rather than implying full translation.
- NetworkX holds the graph in memory — right for this scale, not statewide.
- Grid binning is coarser than kernel density; a cell boundary can split one
  real concentration in two, and the answer says so.
- Catalyst deployment is untested against a live project.

Each of these is surfaced in the product, not only in this file.
