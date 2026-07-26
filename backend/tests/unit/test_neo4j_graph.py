"""Unit and contract tests for Neo4jGraphAdapter.

Tests run entirely without a live Neo4j instance — the adapter's automatic
fallback to in-memory NetworkX is exercised.  A separate integration test
(neo4j_live) would skip unless KSPCIP_GRAPH_BACKEND=neo4j is set.
"""

from __future__ import annotations

from typing import Any

import pytest

from ksp_cip.config.settings import GraphBackend, Settings
from ksp_cip.domain.models import UnitScope
from ksp_cip.infrastructure.db.migrations import apply_migrations
from ksp_cip.infrastructure.db.repositories.intel import GraphRepository
from ksp_cip.infrastructure.db.sqlite_store import SQLiteDataStore
from ksp_cip.infrastructure.graph.neo4j import Neo4jGraphAdapter
from ksp_cip.interface.container import _build_graph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _store(tmp_path: Any) -> SQLiteDataStore:
    db_file = tmp_path / "test_neo4j.db"
    store = SQLiteDataStore(db_file)
    apply_migrations(store)
    store.execute("PRAGMA foreign_keys = OFF", {})
    return store


_EDGES = [
    {
        "edge_id": "edge-1",
        "src_id": "person:p1",
        "src_type": "Person",
        "dst_id": "person:p2",
        "dst_type": "Person",
        "edge_type": "CO_ACCUSED",
        "weight": 1.0,
        "case_ids": [101],
        "unit_ids": [2001],
        "provenance": "source_record",
        "detail": {},
    },
    {
        "edge_id": "edge-2",
        "src_id": "person:p2",
        "src_type": "Person",
        "dst_id": "person:p3",
        "dst_type": "Person",
        "edge_type": "CO_ACCUSED",
        "weight": 3.0,
        "case_ids": [102],
        "unit_ids": [2001],
        "provenance": "inferred",
        "detail": {},
    },
    {
        "edge_id": "edge-3",
        "src_id": "person:p3",
        "src_type": "Person",
        "dst_id": "person:p4",
        "dst_type": "Person",
        "edge_type": "MONEY_FLOW",
        "weight": 5.0,
        "case_ids": [103],
        "unit_ids": [9999],  # different unit — will be trimmed by unit scope
        "provenance": "inferred",
        "detail": {},
    },
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_stats_fallback_has_required_keys(tmp_path: Any) -> None:
    """Stats dict from fallback NetworkX path must have nodes/edges/by_type/backend."""
    repo = GraphRepository(_store(tmp_path))
    repo.replace_all(_EDGES)
    adapter = Neo4jGraphAdapter(repo, uri="bolt://no-neo4j:7687", user="x", password="x")

    stats = adapter.stats()
    assert "nodes" in stats
    assert "edges" in stats
    assert "by_type" in stats
    assert "networkx_fallback" in stats["backend"]
    assert stats["nodes"] == 4
    assert stats["edges"] == 3


def test_expand_fallback_statewide_scope(tmp_path: Any) -> None:
    """Expansion from person:p1 with statewide scope should reach all 4 nodes."""
    repo = GraphRepository(_store(tmp_path))
    repo.replace_all(_EDGES)
    adapter = Neo4jGraphAdapter(repo, uri="bolt://no-neo4j:7687", user="x", password="x")

    scope = UnitScope(statewide=True, unit_ids=tuple(range(1, 9999)))
    result = adapter.expand("person:p1", scope, hops=3, max_nodes=120)

    assert result.seed_node == "person:p1"
    node_ids = {n.node_id for n in result.view.nodes}
    assert "person:p1" in node_ids
    assert "person:p4" in node_ids  # reachable via chain p1→p2→p3→p4


def test_expand_fallback_unit_scope_trim(tmp_path: Any) -> None:
    """Edge to unit 9999 should be trimmed when scope is limited to unit 2001."""
    repo = GraphRepository(_store(tmp_path))
    repo.replace_all(_EDGES)
    adapter = Neo4jGraphAdapter(repo, uri="bolt://no-neo4j:7687", user="x", password="x")

    scope = UnitScope(statewide=False, unit_ids=(2001,))
    result = adapter.expand("person:p1", scope, hops=3, max_nodes=120)

    node_ids = {n.node_id for n in result.view.nodes}
    # p4 is only reachable via the unit-9999 edge → should be trimmed
    assert "person:p4" not in node_ids
    assert result.trimmed_by_scope >= 1


def test_shortest_path_fallback(tmp_path: Any) -> None:
    """Shortest path via fallback NetworkX: p1→p2→p3 should be 2 hops."""
    repo = GraphRepository(_store(tmp_path))
    repo.replace_all(_EDGES)
    adapter = Neo4jGraphAdapter(repo, uri="bolt://no-neo4j:7687", user="x", password="x")

    scope = UnitScope(statewide=True, unit_ids=(2001,))
    result = adapter.shortest_path("person:p1", "person:p3", scope)

    assert result["found"] is True
    assert result["hops"] == 2
    assert result["path"][0] == "person:p1"
    assert result["path"][-1] == "person:p3"


def test_shortest_path_missing_node(tmp_path: Any) -> None:
    """Shortest path between nodes not in graph must return found=False."""
    repo = GraphRepository(_store(tmp_path))
    adapter = Neo4jGraphAdapter(repo, uri="bolt://no-neo4j:7687", user="x", password="x")

    scope = UnitScope(statewide=True, unit_ids=(1,))
    result = adapter.shortest_path("person:ghost", "person:nobody", scope)
    assert result["found"] is False


def test_centrality_fallback(tmp_path: Any) -> None:
    """Centrality returns dict of node_id → float, p2 should be most central."""
    repo = GraphRepository(_store(tmp_path))
    repo.replace_all(_EDGES)
    adapter = Neo4jGraphAdapter(repo, uri="bolt://no-neo4j:7687", user="x", password="x")

    centrality = adapter.centrality(node_prefix="person:")
    assert isinstance(centrality, dict)
    assert "person:p2" in centrality
    # p2 connects to p1 and p3, so highest degree among person: nodes
    assert centrality["person:p2"] == max(centrality.values())


def test_communities_fallback(tmp_path: Any) -> None:
    """Communities must return a dict of node_id → int."""
    repo = GraphRepository(_store(tmp_path))
    repo.replace_all(_EDGES)
    adapter = Neo4jGraphAdapter(repo, uri="bolt://no-neo4j:7687", user="x", password="x")

    comm = adapter.communities()
    assert isinstance(comm, dict)
    assert len(comm) == 4  # four distinct nodes


def test_sync_from_repository_no_neo4j(tmp_path: Any) -> None:
    """sync_from_repository returns 0 gracefully when Neo4j is unreachable."""
    repo = GraphRepository(_store(tmp_path))
    repo.replace_all(_EDGES)
    adapter = Neo4jGraphAdapter(repo, uri="bolt://no-neo4j:7687", user="x", password="x")

    synced = adapter.sync_from_repository()
    assert synced == 0  # driver unavailable → graceful zero


def test_invalidate_clears_fallback_cache(tmp_path: Any) -> None:
    """invalidate() must force the fallback service to be rebuilt on next use."""
    repo = GraphRepository(_store(tmp_path))
    repo.replace_all(_EDGES)
    adapter = Neo4jGraphAdapter(repo, uri="bolt://no-neo4j:7687", user="x", password="x")

    _ = adapter.stats()  # populates fallback
    adapter.invalidate()
    assert adapter._fallback_svc is None
    _ = adapter.stats()  # rebuilds fallback
    assert adapter._fallback_svc is not None


def test_container_graph_backend_switch(tmp_path: Any) -> None:
    """_build_graph must return GraphService for NETWORKX and Neo4jGraphAdapter for NEO4J."""
    repo = GraphRepository(_store(tmp_path))

    svc_nx = _build_graph(Settings(graph_backend=GraphBackend.NETWORKX), repo)
    assert svc_nx.__class__.__name__ == "GraphService"

    svc_neo = _build_graph(Settings(graph_backend=GraphBackend.NEO4J), repo)
    assert svc_neo.__class__.__name__ == "Neo4jGraphAdapter"
