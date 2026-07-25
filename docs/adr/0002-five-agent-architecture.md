# ADR-0002: Exactly five agents, and deterministic services for everything else

**Status:** Accepted · **Date:** 2026-07-25

## Context

The hackathon plan enumerates roughly twelve conversational agents, including
agents for memory, authorization, evidence assembly, PDF generation, audit
logging and language processing. The build mandate fixes the roster at five:
Supervisor, DataRetrieval, CrimeAnalytics, NetworkIntelligence and
InvestigationSupport.

## Decision

There are five agents. The plan's remaining agents are folded in:

| Plan agent | Realised as |
|---|---|
| NLU / orchestration | `SupervisorAgent` |
| Retrieval, RAG | `DataRetrievalAgent` |
| Trend, hotspot, sociology, early warning | `CrimeAnalyticsAgent` |
| Graph, entity resolution, profiling, financial | `NetworkIntelligenceAgent` |
| Decision support | `InvestigationSupportAgent` |
| Memory, authorization, evidence, PDF, audit, language | **Deterministic services** |

The last row is the substantive part. Memory, authorization, evidence
assembly, PDF generation, audit logging and language handling are implemented
as ordinary classes with ordinary tests. They are not agents, they hold no
prompts, and they take no model in their hot path.

`AGENT_ROSTER` is asserted by a test, and `INTENT_ROUTING` is checked for
exhaustiveness at construction: an intent with no route raises at startup
rather than defaulting silently.

## Rationale

Authorization decided by a language model is authorization that can be argued
with. The same applies to what counts as evidence, what goes in an audit row,
and whether a claim is inferred. These are the properties that make the
platform safe for police use, so they belong in code that fails loudly.

Fewer agents also means the routing table fits on a page, which is what makes
"why did it answer that?" a question with an actual answer.

## Consequences

- Adding a capability means extending an existing agent or adding a service —
  not adding a sixth agent.
- Cross-agent chaining is explicit and narrow (`FOLLOW_UP_ROUTING`), gated on
  the first agent having returned something concrete to build on.
