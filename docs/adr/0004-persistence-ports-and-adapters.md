# ADR-0004: One `DataStore` port, three adapters

**Status:** Accepted · **Date:** 2026-07-25

## Context

The platform must run locally for development and evaluation, deploy to
Catalyst for the hackathon, and remain credible as a path to the production
PostgreSQL design — without three codebases.

## Decision

The application layer depends only on the `DataStore`, `FileStore`,
`KeyValueStore`, `LLMGateway`, `LanguageService`, `EmbeddingModel` and
`AuditSink` protocols in `domain/ports`. Adapters implement them:

- `SQLiteDataStore` — thread-local WAL connections, named-parameter binding
  only, nested-safe transactions. Fully functional; used by all tests.
- `CatalystDataStore` — ZCQL over the Data Store REST API.
- `LocalFileStore` / `StratusFileStore`.

Composition happens once, in `interface/container.py`. Switching backend is
`KSPCIP_DATASTORE_BACKEND=catalyst`.

The curated schema is defined once in `schema.sql` and is authoritative. The
loader reads the target table's real columns and conforms rows to them, so a
producer cannot widen the organiser's schema by accident — a dropped column is
logged, not silently accepted.

## Honest limitations

ZCQL is not SQL. The Catalyst adapter refuses statements it cannot faithfully
translate (`PRAGMA`, `ON CONFLICT`) rather than approximating them, and its
`transaction()` is a documented no-op because the Data Store has no multi-row
transaction primitive. The pipeline is designed to be idempotent and
replayable, which is what makes that acceptable.

The Catalyst adapter is covered by contract tests for its translation and
escaping behaviour. **It has not been run against a live Catalyst project in
this build.** That is stated here rather than implied otherwise.
