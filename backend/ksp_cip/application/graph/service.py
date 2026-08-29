"""Graph query service — NetworkX over the materialised edge table.

The graph is loaded once and cached in process, with an explicit
:meth:`invalidate` called by the pipeline after a rebuild. Loading is cheap at
this scale (tens of thousands of edges); the production path swaps this class
for a Neo4j/Cypher adapter behind the same method signatures.

Authorization: expansions are filtered by the caller's unit scope *during*
traversal, so a node reachable only through cases outside the scope is not
returned, and the officer is told the view was trimmed rather than being
shown a silently different graph.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Sequence

import networkx as nx

from ...domain.enums import EdgeType, NodeType, Provenance
from ...domain.models import GraphLink, GraphNode, GraphView, UnitScope
from ...infrastructure.db.repositories import GraphRepository

PERSON_EDGE_TYPES = {str(EdgeType.CO_ACCUSED), str(EdgeType.MONEY_FLOW), str(EdgeType.ARRESTED_BY)}
CASE_EDGE_TYPES = {str(EdgeType.SAME_LOCATION), str(EdgeType.SAME_MODUS_OPERANDI), str(EdgeType.REPEAT_OFFENDER)}


@dataclass(slots=True)
class ExpansionResult:
    view: GraphView
    trimmed_by_scope: int
    edge_type_counts: dict[str, int]
    seed_node: str


class GraphService:
    def __init__(self, repository: GraphRepository) -> None:
        self._repository = repository
        self._lock = threading.RLock()
        self._graph: nx.MultiGraph | None = None
        self._labels: dict[str, str] = {}

    # ------------------------------------------------------------- loading
    def invalidate(self) -> None:
        with self._lock:
            self._graph = None
            self._labels = {}

    @property
    def graph(self) -> nx.MultiGraph:
        with self._lock:
            if self._graph is None:
                self._graph = self._load()
            return self._graph

    def _load(self) -> nx.MultiGraph:
        graph = nx.MultiGraph()
        for edge in self._repository.all_edges():
            source, target = edge["src_id"], edge["dst_id"]
            graph.add_node(source, node_type=edge["src_type"])
            graph.add_node(target, node_type=edge["dst_type"])
            graph.add_edge(
                source,
                target,
                key=edge["edge_id"],
                edge_type=edge["edge_type"],
                weight=float(edge["weight"]),
                case_ids=edge["case_ids"],
                unit_ids=edge["unit_ids"],
                provenance=edge.get("provenance", str(Provenance.INFERRED)),
                detail=edge.get("detail", {}),
            )
        return graph

    def stats(self) -> dict[str, Any]:
        graph = self.graph
        return {
            "nodes": graph.number_of_nodes(),
            "edges": graph.number_of_edges(),
            "by_type": self._repository.stats()["by_type"],
        }

    def set_labels(self, labels: dict[str, str]) -> None:
        with self._lock:
            self._labels = dict(labels)

    def label_for(self, node_id: str) -> str:
        if node_id in self._labels:
            return self._labels[node_id]
        kind, _, rest = node_id.partition(":")
        return f"{kind.capitalize()} {rest}"

    # ----------------------------------------------------------- traversal
    def _edge_allowed(self, data: dict[str, Any], scope: UnitScope) -> bool:
        if scope.statewide:
            return True
        unit_ids = data.get("unit_ids") or []
        if not unit_ids:
            # Edges without unit provenance (e.g. money flow between entities)
            # are only visible when at least one case behind them is in scope,
            # which the caller establishes by seeding from an in-scope node.
            return True
        # Coerced rather than compared directly: scope.unit_ids holds ints, and
        # an edge built while running on the Catalyst Data Store carries its
        # unit ids as strings, because that backend returns every column as a
        # string. Comparing the two silently trimmed every edge as
        # out-of-scope -- failing closed, so nothing leaked, but a scoped
        # officer would have seen an empty graph with no explanation.
        allowed = scope.unit_ids
        for unit_id in unit_ids:
            try:
                if int(unit_id) in allowed:
                    return True
            except (TypeError, ValueError):
                if unit_id in allowed:
                    return True
        return False

    def expand(
        self,
        seed: str,
        scope: UnitScope,
        *,
        hops: int = 2,
        edge_types: Iterable[str] | None = None,
        max_nodes: int = 120,
    ) -> ExpansionResult:
        graph = self.graph
        allowed_types = set(edge_types) if edge_types else None
        trimmed = 0
        counts: dict[str, int] = {}
        if seed not in graph:
            return ExpansionResult(GraphView(), 0, {}, seed)

        frontier = {seed}
        visited: set[str] = {seed}
        kept_edges: dict[str, dict[str, Any]] = {}
        for _hop in range(max(1, hops)):
            next_frontier: set[str] = set()
            for node in frontier:
                for _source, neighbour, key, data in graph.edges(node, keys=True, data=True):
                    if allowed_types and data["edge_type"] not in allowed_types:
                        continue
                    if not self._edge_allowed(data, scope):
                        trimmed += 1
                        continue
                    kept_edges[key] = {"source": node, "target": neighbour, **data}
                    counts[data["edge_type"]] = counts.get(data["edge_type"], 0) + 1
                    if neighbour not in visited and len(visited) < max_nodes:
                        visited.add(neighbour)
                        next_frontier.add(neighbour)
            frontier = next_frontier
            if not frontier:
                break

        nodes = [
            GraphNode(
                node_id=node_id,
                node_type=_node_type(graph.nodes[node_id].get("node_type", "Entity")),
                label=self.label_for(node_id),
                attributes={"seed": node_id == seed},
            )
            for node_id in visited
        ]
        links = [
            GraphLink(
                source=edge["source"],
                target=edge["target"],
                edge_type=EdgeType(edge["edge_type"]) if edge["edge_type"] in EdgeType.__members__.values()
                else EdgeType.CO_ACCUSED,
                weight=edge["weight"],
                case_master_ids=edge["case_ids"],
                provenance=Provenance(edge.get("provenance", "inferred")),
            )
            for edge in kept_edges.values()
            if edge["source"] in visited and edge["target"] in visited
        ]
        subgraph = graph.subgraph(visited)
        view = GraphView(
            nodes=nodes,
            links=links,
            centrality={node: round(value, 4) for node, value in _degree_centrality(subgraph).items()},
            communities=self.communities(subgraph),
        )
        return ExpansionResult(view=view, trimmed_by_scope=trimmed, edge_type_counts=counts, seed_node=seed)

    def shortest_path(self, source: str, target: str, scope: UnitScope) -> dict[str, Any]:
        graph = self.graph
        if source not in graph or target not in graph:
            return {"found": False, "reason": "one or both persons are not present in the link graph"}
        filtered = nx.Graph()
        for u, v, data in graph.edges(data=True):
            if not self._edge_allowed(data, scope):
                continue
            if filtered.has_edge(u, v) and filtered[u][v].get("weight", 0) >= data["weight"]:
                continue
            filtered.add_edge(u, v, weight=data["weight"], edge_type=data["edge_type"],
                              case_ids=data["case_ids"], detail=data.get("detail", {}))
        try:
            path = nx.shortest_path(filtered, source, target)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return {"found": False, "reason": "no path within the authorized scope"}
        steps: list[dict[str, Any]] = []
        for left, right in zip(path, path[1:]):
            data = filtered[left][right]
            steps.append({
                "from": left,
                "from_label": self.label_for(left),
                "to": right,
                "to_label": self.label_for(right),
                "edge_type": data["edge_type"],
                "case_ids": data["case_ids"],
                "detail": data.get("detail", {}),
            })
        return {"found": True, "path": path, "hops": len(path) - 1, "steps": steps}

    def communities(self, graph: nx.Graph | None = None) -> dict[str, int]:
        target = graph if graph is not None else self.graph
        if target.number_of_nodes() == 0:
            return {}
        simple = nx.Graph()
        for u, v, data in target.edges(data=True):
            weight = data.get("weight", 1.0)
            if simple.has_edge(u, v):
                simple[u][v]["weight"] += weight
            else:
                simple.add_edge(u, v, weight=weight)
        try:
            partitions = nx.community.louvain_communities(simple, weight="weight", seed=7)
        except Exception:  # noqa: BLE001 - fall back to a deterministic algorithm
            partitions = nx.community.greedy_modularity_communities(simple, weight="weight")
        assignment: dict[str, int] = {}
        for index, members in enumerate(partitions):
            for node in members:
                assignment[node] = index
        return assignment

    def top_communities(self, *, node_prefix: str = "person:", limit: int = 5, min_size: int = 3) -> list[dict[str, Any]]:
        assignment = self.communities()
        grouped: dict[int, list[str]] = {}
        for node, community in assignment.items():
            if node.startswith(node_prefix):
                grouped.setdefault(community, []).append(node)
        centrality = self.centrality(node_prefix=node_prefix)
        results = []
        for community, members in grouped.items():
            if len(members) < min_size:
                continue
            ranked = sorted(members, key=lambda n: centrality.get(n, 0.0), reverse=True)
            results.append({
                "community_id": community,
                "size": len(members),
                "members": ranked,
                "member_labels": [self.label_for(node) for node in ranked],
                "most_central": ranked[0],
                "most_central_label": self.label_for(ranked[0]),
            })
        results.sort(key=lambda item: item["size"], reverse=True)
        return results[:limit]

    def communities_view(
        self,
        communities: list[dict[str, Any]],
        scope: UnitScope,
        *,
        max_members_per_cluster: int = 12,
        max_nodes: int = 120,
    ) -> GraphView:
        """A renderable subgraph of the top clusters, for the network overview.

        "Show me criminal networks" (no named subject) used to return a plain
        table of cluster sizes. This assembles the actual members and the
        scope-allowed links among them so the console can *draw* the clusters —
        the visualization the brief asks for. Each cluster is capped so a very
        large component does not swamp the picture; the cap is stated in the
        answer, not hidden.
        """
        graph = self.graph
        member_nodes: list[str] = []
        for community in communities:
            member_nodes.extend(community["members"][:max_members_per_cluster])
            if len(member_nodes) >= max_nodes:
                break
        member_set = set(member_nodes[:max_nodes])
        if not member_set:
            return GraphView()

        links: list[GraphLink] = []
        seen_edges: set[tuple[str, str, str]] = set()
        for node in member_set:
            if node not in graph:
                continue
            for _source, neighbour, data in graph.edges(node, data=True):
                if neighbour not in member_set:
                    continue
                if not self._edge_allowed(data, scope):
                    continue
                key = (min(node, neighbour), max(node, neighbour), data["edge_type"])
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                links.append(GraphLink(
                    source=node, target=neighbour,
                    edge_type=EdgeType(data["edge_type"])
                    if data["edge_type"] in EdgeType.__members__.values() else EdgeType.CO_ACCUSED,
                    weight=data["weight"],
                    case_master_ids=data["case_ids"],
                    provenance=Provenance(data.get("provenance", "inferred")),
                ))

        node_community: dict[str, int] = {}
        for community in communities:
            for member in community["members"][:max_members_per_cluster]:
                node_community[member] = community["community_id"]

        nodes = [
            GraphNode(
                node_id=node_id,
                node_type=_node_type(graph.nodes[node_id].get("node_type", "Entity"))
                if node_id in graph else _node_type("Entity"),
                label=self.label_for(node_id),
                attributes={"community": node_community.get(node_id, 0)},
            )
            for node_id in member_set
        ]
        subgraph = graph.subgraph(member_set)
        return GraphView(
            nodes=nodes,
            links=links,
            communities=node_community,
            centrality={node: round(value, 4) for node, value in _degree_centrality(subgraph).items()},
        )

    def organised_activity(
        self,
        scope: UnitScope,
        *,
        case_dates: dict[int, str] | None = None,
        case_districts: dict[int, int] | None = None,
        min_size: int = 3,
        max_size: int = 12,
        min_shared_cases: int = 2,
        min_cohesion: float = 0.30,
        limit: int = 5,
    ) -> list["OrganisedActivitySignal"]:
        """Co-accused clusters that recur across cases rather than once.

        Three properties separate sustained joint activity from people who
        merely appeared on one FIR together, and all three are published:

        * **recurrence** — the cluster shares at least ``min_shared_cases``
          FIRs, so a single multi-accused incident does not qualify;
        * **cohesion** — how densely its members are actually linked. This is a
          hard gate, not a scoring nudge: community detection happily returns a
          38-person component at 5% density, and reporting that as an organised
          group would be a confident answer to a question the data cannot
          support; and
        * **reach** — how many districts and how long a span the cases cover.

        ``max_size`` exists for the same reason. Past roughly a dozen people a
        "community" is a connected region of the co-accused graph rather than a
        group who act together, so it is not reported as one.

        Scope is applied to the underlying edges, so a caller only ever sees
        clusters built from records they are authorised to read.
        """
        graph = self.graph
        assignment = self.communities()
        grouped: dict[int, list[str]] = {}
        for node, community in assignment.items():
            if node.startswith("person:"):
                grouped.setdefault(community, []).append(node)

        signals: list[OrganisedActivitySignal] = []
        for community, members in grouped.items():
            if not (min_size <= len(members) <= max_size):
                continue

            case_counts: dict[int, int] = {}
            edge_types: set[str] = set()
            internal_edges = 0
            member_set = set(members)
            for source, target, data in graph.edges(members, data=True):
                if not self._edge_allowed(data, scope):
                    continue
                if source in member_set and target in member_set:
                    internal_edges += 1
                    edge_types.add(str(data.get("edge_type")))
                for case_id in data.get("case_ids", []):
                    case_counts[int(case_id)] = case_counts.get(int(case_id), 0) + 1

            if not case_counts:
                continue
            # A case touched by more than one internal edge is one the cluster
            # shares, rather than one a single member happens to appear on.
            shared = [c for c, hits in case_counts.items() if hits > 1]
            if len(shared) < min_shared_cases:
                continue

            size = len(members)
            possible = size * (size - 1) / 2
            cohesion = round(min(internal_edges / possible, 1.0), 4) if possible else 0.0
            if cohesion < min_cohesion:
                # Loosely connected people are not a group, however many cases
                # they collectively touch.
                continue

            districts = {case_districts.get(c) for c in case_counts} if case_districts else set()
            districts.discard(None)
            dates = sorted(
                d for d in ((case_dates or {}).get(c) for c in case_counts) if d
            )
            span_days = 0
            if len(dates) >= 2:
                start, end = _parse_day(dates[0]), _parse_day(dates[-1])
                if start and end:
                    span_days = (end - start).days

            # Published weights, like every other score in this platform.
            # Cohesion carries the most weight: acting together is what the
            # signal claims, and breadth without it is just a large component.
            score = round(min(
                cohesion * 40.0
                + min(len(shared) / 6.0, 1.0) * 35.0
                + min(max(len(districts) - 1, 0) / 3.0, 1.0) * 10.0
                + min(span_days / 365.0, 1.0) * 15.0,
                100.0,
            ), 2)
            band = "high" if score >= 65 else "medium" if score >= 40 else "low"

            centrality = self.centrality(node_prefix="person:")
            ranked = sorted(members, key=lambda n: centrality.get(n, 0.0), reverse=True)
            signals.append(OrganisedActivitySignal(
                community_id=community,
                size=size,
                members=ranked,
                member_labels=[self.label_for(node) for node in ranked],
                case_count=len(case_counts),
                case_ids=sorted(case_counts)[:200],
                shared_case_count=len(shared),
                district_count=len(districts),
                first_seen=dates[0] if dates else None,
                last_seen=dates[-1] if dates else None,
                span_days=span_days,
                edge_types=sorted(edge_types),
                cohesion=cohesion,
                score=score,
                band=band,
            ))

        signals.sort(key=lambda s: s.score, reverse=True)
        return signals[:limit]

    def centrality(self, *, node_prefix: str | None = None) -> dict[str, float]:
        graph = self.graph
        if graph.number_of_nodes() == 0:
            return {}
        values = _degree_centrality(graph)
        if node_prefix:
            return {node: value for node, value in values.items() if node.startswith(node_prefix)}
        return values

    def neighbours_of_type(self, node: str, node_type_prefix: str, scope: UnitScope) -> list[str]:
        graph = self.graph
        if node not in graph:
            return []
        found: set[str] = set()
        for _source, neighbour, data in graph.edges(node, data=True):
            if not self._edge_allowed(data, scope):
                continue
            if neighbour.startswith(node_type_prefix):
                found.add(neighbour)
        return sorted(found)

    def case_ids_for_node(self, node: str) -> list[int]:
        graph = self.graph
        if node not in graph:
            return []
        case_ids: set[int] = set()
        for _source, _target, data in graph.edges(node, data=True):
            case_ids.update(int(c) for c in data.get("case_ids", []))
        return sorted(case_ids)


@dataclass(slots=True)
class OrganisedActivitySignal:
    """A co-accused cluster showing the shape of sustained joint activity.

    Deliberately **not** called a gang. Whether a group is an organised
    criminal enterprise is a legal and investigative determination; what the
    records can show is that the same people appear together across several
    FIRs, over a period, sometimes across districts. That is a shape worth
    looking at, and this type carries only that.
    """

    community_id: int
    size: int
    members: list[str]
    member_labels: list[str]
    case_count: int
    case_ids: list[int]
    shared_case_count: int
    district_count: int
    first_seen: str | None
    last_seen: str | None
    span_days: int
    edge_types: list[str]
    cohesion: float
    score: float
    band: str


def _parse_day(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _degree_centrality(graph: nx.Graph) -> dict[str, float]:
    if graph.number_of_nodes() <= 1:
        return {node: 0.0 for node in graph.nodes}
    simple = nx.Graph(graph)
    return nx.degree_centrality(simple)


def _node_type(raw: str) -> NodeType:
    try:
        return NodeType(raw)
    except ValueError:
        return NodeType.ENTITY
