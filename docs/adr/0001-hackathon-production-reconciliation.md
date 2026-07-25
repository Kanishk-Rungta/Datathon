# ADR-0001: Reconciling the production architecture with the hackathon plan

**Status:** Accepted · **Date:** 2026-07-25

## Context

Two frozen documents govern this build. The architecture (v1.0.1) describes a
production system: a Sync Agent replicating from the source FIR database,
PostgreSQL with PostGIS and pgvector, Neo4j for the link graph, a GPU-hosted
LLM, and a five-plane deployment. The hackathon implementation plan constrains
delivery to Zoho Catalyst with synthetic data, no managed Postgres, no Neo4j
and no GPU.

These conflict on infrastructure. They do not conflict on design.

## Decision

Where the two documents disagree on **infrastructure**, the hackathon plan
wins. Where they agree on **design discipline**, the architecture wins and is
implemented in full.

| Concern | Production | This build | Upgrade path |
|---|---|---|---|
| Source ingestion | Sync Agent, CDC from the FIR DB | Synthetic generator writing NDJSON into the same landing-zone layout | Replace the producer; the loader is unchanged |
| Relational store | PostgreSQL + PostGIS | SQLite (local) / Catalyst Data Store, behind one `DataStore` port | Add a Postgres adapter |
| Vector search | pgvector + BGE-M3 | Fitted hashed char-n-gram TF-IDF, in-memory cosine | Swap `EmbeddingModel`; the retrieval interface is unchanged |
| Graph | Neo4j + Cypher | `cip_graph_edge` rows traversed with NetworkX | Node ids and edge types match the Neo4j model one-for-one |
| Geospatial | PostGIS kernel density | Equal-area grid binning | Same `HotspotCell` output contract |
| LLM | Self-hosted on GPU | Pluggable gateway, local deterministic provider by default | Change one environment variable |

What is kept in full from the architecture: medallion layering (raw → curated →
intelligence), hash-diff change detection, the control tables, the data-quality
gate, unit-scoped authorization pushed into SQL, the append-only audit trail,
the evidence contract, and the §15 schema remedies.

## Consequences

- The platform runs locally with zero credentials and deploys to Catalyst
  without an architectural change.
- Each substitution is a scale decision with a named upgrade path, not a
  different design. No substitution weakens a safety property.
- The cost is honest: NetworkX will not hold a statewide graph in memory, and
  grid binning is coarser than kernel density. Both are stated in the UI and
  in the computation traces rather than hidden.
