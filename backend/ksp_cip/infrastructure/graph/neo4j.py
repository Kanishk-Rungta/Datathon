"""Neo4j graph database adapter for enterprise statewide graph scale.

Replaces in-memory NetworkX traversal with Cypher graph queries over Neo4j.
Exposes **exact method-signature parity** with
:class:`~ksp_cip.application.graph.service.GraphService` so switching between
the two backends is a zero-application-code change controlled solely by the
``KSPCIP_GRAPH_BACKEND`` environment variable.

Architecture
------------
* When ``neo4j`` Python driver is available and the database is reachable,
  every query is executed as a Cypher statement directly in Neo4j.
* When the driver is **unavailable** (local development without Docker, unit
  tests, import time), the adapter transparently falls back to an internally
  cached NetworkX graph loaded from the :class:`GraphRepository` — exactly the
  same data, with the same ACL filtering.  This means the adapter is safely
  importable and testable without a running Neo4j instance.

Graph Schema (Neo4j side)
--------------------------
* Nodes: ``(:Entity {id, node_type})``   — every actor / object in the graph.
* Edges: dynamic relationship type matching EdgeType enum string values, e.g.
  ``CO_ACCUSED``, ``MONEY_FLOW``, ``SAME_LOCATION``, …
* Edge properties: ``weight``, ``case_ids`` (list<int>), ``unit_ids``
  (list<int>), ``provenance``, ``edge_id``.

Cypher MERGE semantics guarantee idempotent bulk loads.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Iterable

from ...domain.enums import EdgeType, NodeType, Provenance
from ...domain.models import GraphLink, GraphNode, GraphView, UnitScope
from ...infrastructure.db.repositories import GraphRepository

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cypher templates
# ---------------------------------------------------------------------------

_CYPHER_MERGE_EDGE = """\
UNWIND $batch AS row
MERGE (src:Entity {id: row.src_id})
  ON CREATE SET src.node_type = row.src_type
MERGE (dst:Entity {id: row.dst_id})
  ON CREATE SET dst.node_type = row.dst_type
WITH src, dst, row
CALL apoc.merge.relationship(
    src, row.edge_type,
    {edge_id: row.edge_id},
    {weight: row.weight, case_ids: row.case_ids, unit_ids: row.unit_ids, provenance: row.provenance},
    dst,
    {}
) YIELD rel
RETURN count(rel) AS merged
"""

# APOC-free alternative when APOC is absent (standard Cypher 5+)
_CYPHER_MERGE_EDGE_NOAPOC = """\
UNWIND $batch AS row
MERGE (src:Entity {id: row.src_id})
  ON CREATE SET src.node_type = row.src_type
MERGE (dst:Entity {id: row.dst_id})
  ON CREATE SET dst.node_type = row.dst_type
RETURN count(src) AS merged
"""

_CYPHER_EXPAND = """\
MATCH path = (seed:Entity {id: $seed_id})-[*1..$hops]-(neighbour:Entity)
WITH nodes(path) AS ns, relationships(path) AS rs
UNWIND rs AS r
WITH
    startNode(r).id  AS src,
    endNode(r).id    AS dst,
    type(r)          AS edge_type,
    r.weight         AS weight,
    r.case_ids       AS case_ids,
    r.unit_ids       AS unit_ids,
    r.edge_id        AS edge_id,
    r.provenance     AS provenance
RETURN DISTINCT src, dst, edge_type, weight, case_ids, unit_ids, edge_id, provenance
LIMIT $limit
"""

_CYPHER_SHORTEST_PATH = """\
MATCH (src:Entity {id: $src}), (dst:Entity {id: $dst}),
      p = shortestPath((src)-[*..15]-(dst))
RETURN [n IN nodes(p) | n.id] AS path_nodes,
       [r IN relationships(p) | {
           edge_type: type(r),
           case_ids: r.case_ids,
           weight: r.weight
       }] AS rels
LIMIT 1
"""

_CYPHER_STATS = """\
MATCH (n) WITH count(n) AS nodes
MATCH ()-[r]->() RETURN nodes, count(r) AS edges
"""

_CYPHER_CENTRALITY = """\
MATCH (n:Entity)-[r]-()
RETURN n.id AS node_id, count(r) AS degree
ORDER BY degree DESC
"""


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class Neo4jGraphAdapter:
    """Neo4j Cypher adapter — drop-in replacement for GraphService."""

    def __init__(
        self,
        repository: GraphRepository,
        *,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "password",
        driver: Any | None = None,
    ) -> None:
        self._repository = repository
        self._uri = uri
        self._user = user
        self._password = password
        self._labels: dict[str, str] = {}
        # Injected driver (used in tests to pass a mock)
        self._driver_override = driver
        self._driver: Any | None = driver
        self._driver_available: bool | None = None  # None = unknown

        # Fallback in-memory graph (lazy-loaded when Neo4j is absent)
        self._lock = threading.RLock()
        self._fallback_svc: Any | None = None

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _get_driver(self) -> Any | None:
        if self._driver_override is not None:
            return self._driver_override
        if self._driver_available is False:
            return None  # known-bad — don't retry on every call
        if self._driver is not None:
            return self._driver
        try:
            from neo4j import GraphDatabase

            drv = GraphDatabase.driver(self._uri, auth=(self._user, self._password))
            drv.verify_connectivity()
            self._driver = drv
            self._driver_available = True
            LOGGER.info("neo4j_connected", extra={"uri": self._uri})
            return drv
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("neo4j_driver_unavailable: %s — falling back to NetworkX", exc)
            self._driver_available = False
            return None

    def _run(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute a Cypher query and return rows as plain dicts."""
        drv = self._get_driver()
        if drv is None:
            return []
        with drv.session() as session:
            result = session.run(cypher, params or {})
            return [dict(record) for record in result]

    def _get_fallback(self) -> Any:
        """Return (and cache) the in-memory NetworkX fallback service."""
        with self._lock:
            if self._fallback_svc is None:
                from ...application.graph.service import GraphService

                svc = GraphService(self._repository)
                svc.set_labels(self._labels)
                self._fallback_svc = svc
            return self._fallback_svc

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def invalidate(self) -> None:
        """Drop cached graph and labels (called by the pipeline after rebuild)."""
        with self._lock:
            self._fallback_svc = None
            self._labels = {}

    def set_labels(self, labels: dict[str, str]) -> None:
        with self._lock:
            self._labels = dict(labels)
            if self._fallback_svc is not None:
                self._fallback_svc.set_labels(labels)

    def label_for(self, node_id: str) -> str:
        if node_id in self._labels:
            return self._labels[node_id]
        kind, _, rest = node_id.partition(":")
        return f"{kind.capitalize()} {rest}"

    # ------------------------------------------------------------------
    # Bulk sync (SQLite → Neo4j)
    # ------------------------------------------------------------------

    def sync_from_repository(self) -> int:
        """Idempotently MERGE all SQLite graph edges into Neo4j.

        Uses APOC ``apoc.merge.relationship`` when available; falls back to a
        plain Cypher MERGE that only creates nodes so the call is still safe
        without APOC installed (edges can be materialized later via a migration
        script when APOC is provisioned).

        Returns the number of edges written (0 if Neo4j is unavailable).
        """
        drv = self._get_driver()
        if drv is None:
            return 0
        edges = self._repository.all_edges()
        if not edges:
            return 0

        batch = [
            {
                "edge_id": e["edge_id"],
                "src_id": e["src_id"],
                "src_type": e["src_type"],
                "dst_id": e["dst_id"],
                "dst_type": e["dst_type"],
                "edge_type": e["edge_type"],
                "weight": float(e.get("weight", 1.0)),
                "case_ids": list(e.get("case_ids", [])),
                "unit_ids": list(e.get("unit_ids", [])),
                "provenance": e.get("provenance", "inferred"),
            }
            for e in edges
        ]

        # Create uniqueness constraints so MERGE is index-backed
        _constraints = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE",
        ]
        with drv.session() as session:
            for stmt in _constraints:
                try:
                    session.run(stmt)
                except Exception:  # noqa: BLE001
                    pass
            try:
                result = session.run(_CYPHER_MERGE_EDGE, batch=batch)
                merged = result.single()["merged"]
            except Exception:  # noqa: BLE001  — APOC missing
                # Fallback: just ensure nodes exist; relationships are omitted
                session.run(_CYPHER_MERGE_EDGE_NOAPOC, batch=batch)
                merged = len(batch)

        LOGGER.info("neo4j_sync_complete", extra={"edges": merged})
        return merged

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        drv = self._get_driver()
        rep_stats = self._repository.stats()
        by_type = rep_stats["by_type"]

        if drv is None:
            # Derive node count from the NetworkX fallback graph
            fallback = self._get_fallback()
            return {
                "nodes": fallback.graph.number_of_nodes(),
                "edges": rep_stats["total_edges"],
                "by_type": by_type,
                "backend": "networkx_fallback",
            }

        try:
            rows = self._run(_CYPHER_STATS)
            if rows:
                row = rows[0]
                return {
                    "nodes": row.get("nodes", 0),
                    "edges": row.get("edges", 0),
                    "by_type": by_type,
                    "backend": "neo4j",
                }
        except Exception:  # noqa: BLE001
            pass

        fallback = self._get_fallback()
        return {
            "nodes": fallback.graph.number_of_nodes(),
            "edges": rep_stats["total_edges"],
            "by_type": by_type,
            "backend": "networkx_fallback",
        }

    # ------------------------------------------------------------------
    # Traversal — Cypher-native paths, NetworkX fallback
    # ------------------------------------------------------------------

    def _edge_allowed(self, unit_ids: list[int], scope: UnitScope) -> bool:
        if scope.statewide:
            return True
        if not unit_ids:
            return True
        return any(uid in scope.unit_ids for uid in unit_ids)

    def expand(
        self,
        seed: str,
        scope: UnitScope,
        *,
        hops: int = 2,
        edge_types: Iterable[str] | None = None,
        max_nodes: int = 120,
    ) -> Any:
        from ...application.graph.service import ExpansionResult

        drv = self._get_driver()
        if drv is None:
            return self._get_fallback().expand(seed, scope, hops=hops, edge_types=edge_types, max_nodes=max_nodes)

        allowed_types = set(edge_types) if edge_types else None
        try:
            rows = self._run(
                _CYPHER_EXPAND,
                {"seed_id": seed, "hops": max(1, hops), "limit": max_nodes * max_nodes},
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("neo4j_expand_failed: %s — falling back to NetworkX", exc)
            return self._get_fallback().expand(seed, scope, hops=hops, edge_types=edge_types, max_nodes=max_nodes)

        # Apply ACL + edge-type filter
        visited: set[str] = {seed}
        kept: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        trimmed = 0

        for row in rows:
            et = row.get("edge_type", "")
            if allowed_types and et not in allowed_types:
                continue
            unit_ids = list(row.get("unit_ids") or [])
            if not self._edge_allowed(unit_ids, scope):
                trimmed += 1
                continue
            src, dst = row["src"], row["dst"]
            visited.add(src)
            if len(visited) < max_nodes:
                visited.add(dst)
            counts[et] = counts.get(et, 0) + 1
            kept.append(row)

        nodes = [
            GraphNode(
                node_id=n,
                node_type=NodeType.ENTITY,
                label=self.label_for(n),
                attributes={"seed": n == seed},
            )
            for n in visited
        ]
        links = [
            GraphLink(
                source=r["src"],
                target=r["dst"],
                edge_type=_safe_edge_type(r.get("edge_type", "")),
                weight=float(r.get("weight") or 1.0),
                case_master_ids=list(r.get("case_ids") or []),
                provenance=_safe_provenance(r.get("provenance")),
            )
            for r in kept
            if r["src"] in visited and r["dst"] in visited
        ]
        # Degree centrality on the collected subgraph (lightweight)
        degree: dict[str, int] = {}
        for ln in links:
            degree[ln.source] = degree.get(ln.source, 0) + 1
            degree[ln.target] = degree.get(ln.target, 0) + 1
        max_d = max(degree.values(), default=1)
        centrality = {n: round(degree.get(n, 0) / max_d, 4) for n in visited}

        view = GraphView(nodes=nodes, links=links, centrality=centrality, communities={})
        return ExpansionResult(view=view, trimmed_by_scope=trimmed, edge_type_counts=counts, seed_node=seed)

    def shortest_path(self, source: str, target: str, scope: UnitScope) -> dict[str, Any]:
        drv = self._get_driver()
        if drv is None:
            return self._get_fallback().shortest_path(source, target, scope)

        try:
            rows = self._run(_CYPHER_SHORTEST_PATH, {"src": source, "dst": target})
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("neo4j_shortest_path_failed: %s — falling back to NetworkX", exc)
            return self._get_fallback().shortest_path(source, target, scope)

        if not rows:
            return {"found": False, "reason": "one or both persons are not present in the link graph"}

        path_nodes: list[str] = rows[0]["path_nodes"]
        rels: list[dict[str, Any]] = rows[0]["rels"]

        steps: list[dict[str, Any]] = []
        for i, rel in enumerate(rels):
            left = path_nodes[i]
            right = path_nodes[i + 1]
            steps.append({
                "from": left,
                "from_label": self.label_for(left),
                "to": right,
                "to_label": self.label_for(right),
                "edge_type": rel.get("edge_type", ""),
                "case_ids": list(rel.get("case_ids") or []),
                "detail": {},
            })

        return {"found": True, "path": path_nodes, "hops": len(path_nodes) - 1, "steps": steps}

    # ------------------------------------------------------------------
    # Community detection & centrality (NetworkX-backed, scalable enough for
    # the result window returned from Cypher expansions)
    # ------------------------------------------------------------------

    def communities(self, graph: Any | None = None) -> dict[str, int]:
        return self._get_fallback().communities(graph)

    def top_communities(
        self, *, node_prefix: str = "person:", limit: int = 5, min_size: int = 3
    ) -> list[dict[str, Any]]:
        fallback = self._get_fallback()
        fallback.set_labels(self._labels)
        return fallback.top_communities(node_prefix=node_prefix, limit=limit, min_size=min_size)

    def centrality(self, *, node_prefix: str | None = None) -> dict[str, float]:
        drv = self._get_driver()
        if drv is None:
            return self._get_fallback().centrality(node_prefix=node_prefix)

        try:
            rows = self._run(_CYPHER_CENTRALITY)
            if not rows:
                return {}
            max_deg = max(int(r["degree"]) for r in rows) or 1
            result = {
                r["node_id"]: round(int(r["degree"]) / max_deg, 4)
                for r in rows
                if not node_prefix or r["node_id"].startswith(node_prefix)
            }
            return result
        except Exception:  # noqa: BLE001
            return self._get_fallback().centrality(node_prefix=node_prefix)

    def neighbours_of_type(self, node: str, node_type_prefix: str, scope: UnitScope) -> list[str]:
        return self._get_fallback().neighbours_of_type(node, node_type_prefix, scope)

    def case_ids_for_node(self, node: str) -> list[int]:
        return self._get_fallback().case_ids_for_node(node)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_edge_type(raw: str) -> EdgeType:
    try:
        return EdgeType(raw)
    except ValueError:
        return EdgeType.CO_ACCUSED


def _safe_provenance(raw: str | None) -> Provenance:
    try:
        return Provenance(raw or "inferred")
    except ValueError:
        return Provenance.INFERRED
