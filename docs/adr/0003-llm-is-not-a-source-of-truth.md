# ADR-0003: The language model orchestrates and phrases; it never knows

**Status:** Accepted · **Date:** 2026-07-25

## Context

The platform answers questions from police records. A fluent, confident, wrong
answer is worse here than no answer: it may be acted on.

## Decision

The LLM is permitted exactly three jobs:

1. **Intent refinement** — only when deterministic rules are below confidence,
   and only if it returns a label already in the fixed taxonomy.
2. **Narrative phrasing** — rewriting an already-composed, already-evidenced
   answer for readability.
3. **Translation and speech**, via the language service, when Bhashini is
   configured.

It is forbidden from: producing a figure, choosing a filter, generating SQL,
deciding authorization, deciding what counts as evidence, or introducing a
fact absent from the deterministic payload.

Three mechanisms enforce this rather than three sentences of prompt:

- **Deterministic-first routing.** Rules classify; the model is consulted only
  on low confidence and its answer is rejected unless it names a known label.
- **Evidence enforcement.** `AnswerComposer._enforce_evidence` raises
  `EvidenceMissingError` on any numeric or inferred claim lacking a locator,
  or citing a locator that was not published. The answer never leaves.
- **Rewrite verification.** `verify_rewrite` re-checks a polished answer:
  citations must match exactly, no number may appear that was not in the
  draft, and provenance markers must survive. A failed check discards the
  rewrite and ships the deterministic draft.

The default provider is `local`: a deterministic, offline composer that reads
the `FACTS:` block and returns narrative built only from it. The platform
therefore runs, and its tests pass, with no credentials and no network.

## Consequences

- Answer quality degrades gracefully to "plain but correct" rather than
  failing or fabricating when no model is configured.
- Tests are hermetic and deterministic.
- Prompt injection through case text cannot change a figure, because no figure
  is ever produced by a model.
