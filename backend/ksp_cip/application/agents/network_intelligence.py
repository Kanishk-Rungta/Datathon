"""NetworkIntelligenceAgent — link analysis, identity, offender history, money.

Absorbs the plan's Graph, Entity-Resolution, Profiling and Financial agents.
Three disciplines are enforced here rather than left to prompt wording:

* **Inference is labelled.** Every edge this agent reports is derived, so each
  claim built from one carries ``Provenance.INFERRED`` and the composer renders
  it with an explicit "(inferred)" marker.
* **Identity is provisional.** Resolved identities are reported with their
  match basis, and anything below the auto-link threshold is surfaced as a
  review item, never silently merged.
* **Financial data is an extension.** The FIR schema has no transactions; every
  money claim is tagged ``SYNTHETIC_EXTENSION`` and rendered with that marker.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from ...domain.enums import AgentName, EdgeType, Intent, Permission, Provenance
from ...domain.models import AgentResult, StructuredPayload, UnitScope
from ...infrastructure.db.repositories import (
    CaseRepository,
    FinancialRepository,
    IdentityRepository,
)
from ..graph import FinancialAnalyzer, GraphService, person_node
from ..graph.financial import (
    BURST_BASELINE_DAYS,
    BURST_Z_THRESHOLD,
    CHAIN_WINDOW_DAYS,
    CONCENTRATION_PERCENTILE,
    MIN_ACCOUNTS_FOR_CONCENTRATION,
    STRUCTURING_THRESHOLD,
)
from ..services.audit import AuditService, audited
from ..services.authorization import AuthorizationService
from ..services.evidence import (
    aggregate_evidence,
    case_evidence,
    claim,
    edge_evidence,
    empty_result_evidence,
    person_evidence,
    trace,
    transaction_evidence,
)
from .base import AgentRequest, BaseAgent

EDGE_PHRASING = {
    str(EdgeType.CO_ACCUSED): "named as accused in the same FIR",
    str(EdgeType.SAME_LOCATION): "incidents recorded in the same locality grid cell",
    str(EdgeType.SAME_MODUS_OPERANDI): "same offence type within a short time and distance window",
    str(EdgeType.REPEAT_OFFENDER): "the same resolved person appears in both FIRs",
    str(EdgeType.ARRESTED_BY): "arrest recorded by the same investigating officer",
    str(EdgeType.MONEY_FLOW): "transfer recorded in the synthetic financial extension",
    "ALLEGED_IN": "named as accused in this FIR",
}


class NetworkIntelligenceAgent(BaseAgent):
    name = AgentName.NETWORK_INTELLIGENCE

    def __init__(
        self,
        audit: AuditService,
        graph: GraphService,
        identities: IdentityRepository,
        cases: CaseRepository,
        financial: FinancialRepository,
        analyzer: FinancialAnalyzer,
        authorization: AuthorizationService,
    ) -> None:
        super().__init__(audit)
        self._graph = graph
        self._identities = identities
        self._cases = cases
        self._financial = financial
        self._analyzer = analyzer
        self._authorization = authorization

    @audited("agent.network_intelligence", object_type="graph")
    def handle(self, request: AgentRequest) -> AgentResult:
        request.principal.require(Permission.USE_GRAPH_TOOLS)
        if request.intent is Intent.OFFENDER_PROFILE:
            return self._offender_profile(request)
        if request.intent is Intent.FINANCIAL_LINK:
            return self._financial_links(request)
        return self._network(request)

    # -------------------------------------------------------------- network
    def _network(self, request: AgentRequest) -> AgentResult:
        names = request.slots.person_names or request.pinned_person_names
        if len(names) >= 2:
            return self._path_between(request, names[0], names[1])
        if names:
            return self._ego_network(request, names[0])
        case_ids = request.slots.case_master_ids or request.pinned_case_master_ids
        if request.slots.crime_nos:
            summary = self._cases.by_crime_no(request.slots.crime_nos[0], request.scope)
            if summary:
                case_ids = [summary.case_master_id]
        if case_ids:
            return self._case_network(request, case_ids[0])
        return self._top_communities(request)

    def _resolve_identity(self, name: str) -> dict[str, Any] | None:
        candidates = self._identities.identities()
        target = name.casefold()
        exact = [i for i in candidates if str(i["canonical_name"]).casefold() == target]
        if exact:
            return max(exact, key=lambda i: len(i.get("case_ids", [])))
        partial = [i for i in candidates if target in str(i["canonical_name"]).casefold()]
        if partial:
            return max(partial, key=lambda i: len(i.get("case_ids", [])))
        return None

    def _ego_network(self, request: AgentRequest, name: str) -> AgentResult:
        identity = self._resolve_identity(name)
        if identity is None:
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(
                    f"'{name}' does not appear as an accused in the indexed FIRs, so there is no link "
                    "structure to show."
                )],
                traces=[trace("identity_lookup",
                              "Matched the requested name against resolved identities built from curated_Accused.",
                              inputs={"name": name}, row_count=0)],
                confidence=0.8,
            )

        seed = person_node(str(identity["identity_id"]))
        hops = int(request.options.get("hops", 2))
        expansion = self._graph.expand(seed, request.scope, hops=hops)
        view = expansion.view
        if not view.links:
            solo = person_evidence(
                identity_id=str(identity["identity_id"]),
                label=str(identity["canonical_name"]),
                case_master_ids=[int(c) for c in identity.get("case_ids", [])],
                crime_nos=[str(n) for n in identity.get("crime_nos", [])],
            )
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(
                    f"{identity['canonical_name']} appears in {len(identity.get('case_ids', []))} FIR(s) but has "
                    "no co-accused or other link to another person within your authorized scope.",
                    [solo], provenance=Provenance.INFERRED,
                )],
                evidence=[solo],
                traces=[self._graph_trace(expansion, hops)],
                confidence=0.85,
            )

        person_links = [l for l in view.links if l.source.startswith("person:") and l.target.startswith("person:")]
        associates = sorted({
            node for link in person_links for node in (link.source, link.target) if node != seed
        })
        evidence_items = [person_evidence(
            identity_id=str(identity["identity_id"]),
            label=str(identity["canonical_name"]),
            case_master_ids=[int(c) for c in identity.get("case_ids", [])],
            crime_nos=[str(n) for n in identity.get("crime_nos", [])],
        )]
        claims = [claim(
            f"{identity['canonical_name']} is linked to {len(associates)} other person(s) within "
            f"{hops} hop(s) of the link graph.",
            [evidence_items[0]], provenance=Provenance.INFERRED,
        )]

        for link in sorted(person_links, key=lambda l: l.weight, reverse=True)[:5]:
            other = link.target if link.source == seed else link.source
            if other == seed:
                continue
            item = edge_evidence(
                edge_id=f"{link.edge_type}:{link.source}->{link.target}",
                label=f"{self._graph.label_for(link.source)} ↔ {self._graph.label_for(link.target)}",
                case_master_ids=link.case_master_ids,
                crime_nos=link.crime_nos,
                detail={"basis": EDGE_PHRASING.get(str(link.edge_type), str(link.edge_type)),
                        "edge_type": str(link.edge_type)},
            )
            evidence_items.append(item)
            claims.append(claim(
                f"{self._graph.label_for(other)} — {EDGE_PHRASING.get(str(link.edge_type), str(link.edge_type))} "
                f"({len(link.case_master_ids)} shared case reference(s)).",
                [item], provenance=Provenance.INFERRED,
            ))

        if expansion.trimmed_by_scope:
            claims.append(claim(
                f"{expansion.trimmed_by_scope} link(s) were withheld because they run through police stations "
                "outside your authorized scope."
            ))
        claims.append(claim(
            "Links are derived from shared FIR records, not from any confirmed association. Co-accused in one "
            "FIR does not establish an ongoing relationship."
        ))

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=evidence_items,
            traces=[self._graph_trace(expansion, hops)],
            payload=self._graph_payload(f"Link network — {identity['canonical_name']}", view, seed),
            data={"seed": seed, "identity_id": identity["identity_id"],
                  "case_master_ids": self._graph.case_ids_for_node(seed)[:200]},
        )

    def _path_between(self, request: AgentRequest, left_name: str, right_name: str) -> AgentResult:
        left = self._resolve_identity(left_name)
        right = self._resolve_identity(right_name)
        missing = [name for name, found in ((left_name, left), (right_name, right)) if found is None]
        if missing:
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(
                    "Not found in the indexed accused records: " + ", ".join(f"'{m}'" for m in missing) + "."
                )],
                traces=[trace("identity_lookup", "Matched both names against resolved identities.",
                              inputs={"names": [left_name, right_name]}, row_count=0)],
                confidence=0.8,
            )

        source = person_node(str(left["identity_id"]))
        target = person_node(str(right["identity_id"]))
        result = self._graph.shortest_path(source, target, request.scope)
        if not result["found"]:
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(
                    f"No link path connects {left['canonical_name']} and {right['canonical_name']} — "
                    f"{result['reason']}."
                )],
                traces=[trace("shortest_path",
                              "Searched the scope-filtered link graph for a shortest path between the two persons.",
                              inputs={"source": source, "target": target}, row_count=0)],
                confidence=0.85,
            )

        evidence_items = []
        claims = [claim(
            f"{left['canonical_name']} and {right['canonical_name']} are connected through "
            f"{result['hops']} link(s).",
            provenance=Provenance.INFERRED,
        )]
        for step in result["steps"]:
            item = edge_evidence(
                edge_id=f"{step['edge_type']}:{step['from']}->{step['to']}",
                label=f"{step['from_label']} ↔ {step['to_label']}",
                case_master_ids=[int(c) for c in step["case_ids"]],
                crime_nos=[],
                detail={**step.get("detail", {}), "edge_type": step["edge_type"]},
            )
            evidence_items.append(item)
            claims.append(claim(
                f"{step['from_label']} → {step['to_label']}: "
                f"{EDGE_PHRASING.get(step['edge_type'], step['edge_type'])}.",
                [item], provenance=Provenance.INFERRED,
            ))
        claims[0].evidence_locators = [evidence_items[0].locator]
        claims.append(claim(
            "A path in this graph means a chain of shared records exists. It is not evidence that the two people "
            "know each other."
        ))

        expansion = self._graph.expand(source, request.scope, hops=2, max_nodes=60)
        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=evidence_items,
            traces=[trace(
                "shortest_path",
                "Collapsed the multigraph to its lightest edge per pair, filtered it to your authorized units, "
                "then took the shortest path by hop count.",
                inputs={"source": source, "target": target, "hops": result["hops"]},
                row_count=len(result["steps"]),
            )],
            payload=self._graph_payload(
                f"Connection — {left['canonical_name']} to {right['canonical_name']}", expansion.view, source
            ),
            data={"path": result["path"], "hops": result["hops"]},
        )

    def _case_network(self, request: AgentRequest, case_master_id: int) -> AgentResult:
        summary = self._cases.by_id(case_master_id, request.scope)
        if summary is None:
            return self.empty_result(request, "That FIR is not available within your authorized scope.")
        seed = f"case:{case_master_id}"
        expansion = self._graph.expand(seed, request.scope, hops=2, max_nodes=80)
        view = expansion.view
        if not view.links:
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(f"FIR {summary.crime_no} has no derived links to other records.",
                                      [case_evidence(summary)])],
                traces=[self._graph_trace(expansion, 2)], confidence=0.85,
            )
        evidence_items = [case_evidence(summary)]
        counts = expansion.edge_type_counts
        claims = [claim(
            f"FIR {summary.crime_no} connects to {len(view.nodes) - 1} other node(s) in the link graph.",
            [evidence_items[0]], provenance=Provenance.INFERRED,
        )]
        for edge_type, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:4]:
            claims.append(claim(
                f"{count} link(s) of type {edge_type} — {EDGE_PHRASING.get(edge_type, edge_type)}.",
                [evidence_items[0]], provenance=Provenance.INFERRED,
            ))
        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=evidence_items,
            traces=[self._graph_trace(expansion, 2)],
            payload=self._graph_payload(f"Links around FIR {summary.crime_no}", view, seed),
            data={"case_master_ids": [case_master_id]},
        )

    def _top_communities(self, request: AgentRequest) -> AgentResult:
        communities = self._graph.top_communities(limit=5)
        if not communities:
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim("The link graph contains no person cluster large enough to report.")],
                traces=[trace("community_detection",
                              "Ran Louvain modularity community detection over the person link graph.",
                              row_count=0)],
                confidence=0.8,
            )
        evidence_items = []
        claims = [claim(
            f"{len(communities)} clusters of connected persons stand out in the link graph.",
            provenance=Provenance.INFERRED,
        )]
        for index, community in enumerate(communities, start=1):
            case_ids = self._graph.case_ids_for_node(community["most_central"])
            item = aggregate_evidence(
                key=f"community:{community['community_id']}",
                label=f"Cluster {index}: {community['size']} persons",
                case_master_ids=case_ids[:50],
                detail={"members": community["member_labels"][:10]},
            )
            evidence_items.append(item)
            claims.append(claim(
                f"Cluster {index}: {community['size']} people, most connected member "
                f"{community['most_central_label']}.",
                [item], provenance=Provenance.INFERRED,
            ))
        claims[0].evidence_locators = [evidence_items[0].locator]
        claims.append(claim(
            "Clusters are computed from shared FIR records by a modularity algorithm. They indicate where to "
            "look, not who is organised."
        ))

        # Render the clusters as a graph the console can draw — the network
        # visualization the brief asks for — falling back to a table only if the
        # subgraph came back empty (e.g. everything trimmed by scope).
        view = self._graph.communities_view(communities, request.scope)
        if view.nodes:
            payload = self._graph_payload("Criminal networks — top clusters", view, seed="")
        else:
            payload = StructuredPayload(
                payload_type="table", title="Person clusters",
                data={
                    "columns": ["Cluster", "Members", "Most connected"],
                    "rows": [[str(c["community_id"]), str(c["size"]), c["most_central_label"]]
                             for c in communities],
                },
            )

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=evidence_items,
            traces=[trace(
                "community_detection",
                "Collapsed the link multigraph to a weighted simple graph and ran Louvain modularity "
                "optimisation with a fixed seed, then ranked members by degree centrality.",
                inputs={"clusters": len(communities)}, row_count=len(communities),
            )],
            payload=payload,
            data={"communities": [c["community_id"] for c in communities]},
        )

    # ----------------------------------------------------- offender profile
    def _offender_profile(self, request: AgentRequest) -> AgentResult:
        names = request.slots.person_names or request.pinned_person_names
        if names:
            identity = self._resolve_identity(names[0])
            if identity is None:
                return self.empty_result(request, f"'{names[0]}' is not present in the indexed accused records.")
            record = self._identities.offender(str(identity["identity_id"]))
            if record is None:
                single = person_evidence(
                    identity_id=str(identity["identity_id"]),
                    label=str(identity["canonical_name"]),
                    case_master_ids=[int(c) for c in identity.get("case_ids", [])],
                    crime_nos=[str(n) for n in identity.get("crime_nos", [])],
                )
                return AgentResult(
                    agent=self.name, intent=request.intent,
                    summary_claims=[claim(
                        f"{identity['canonical_name']} appears in "
                        f"{len(identity.get('case_ids', []))} FIR(s); with fewer than two linked cases no "
                        "repeat-offence history is computed.",
                        [single], provenance=Provenance.INFERRED,
                    )],
                    evidence=[single],
                    traces=[trace("offender_scoring", "Looked up the stored repeat-offender record.",
                                  inputs={"identity_id": identity["identity_id"]}, row_count=0)],
                    confidence=0.85,
                )
            return self._offender_result(request, [record], single=True)

        records = self._identities.top_offenders(
            limit=request.slots.limit or 10,
            unit_ids=None if request.scope.statewide else sorted(request.scope.unit_ids),
        )
        if not records:
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(
                    "No person in your authorized scope is linked to more than one FIR, so there is no "
                    "repeat-offence history to report."
                )],
                traces=[trace("offender_scoring", "Queried stored repeat-offender scores within scope.",
                              row_count=0)],
                confidence=0.85,
            )
        return self._offender_result(request, records, single=False)

    def _offender_result(self, request: AgentRequest, records: list[dict[str, Any]], *, single: bool) -> AgentResult:
        evidence_items = []
        claims: list[Any] = []
        if not single:
            claims.append(claim(
                f"{len(records)} person(s) in your scope are named as accused in more than one FIR.",
                provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        for record in records[:8]:
            identity = self._identities.identity(str(record["identity_id"])) or {}
            item = person_evidence(
                identity_id=str(record["identity_id"]),
                label=str(record["canonical_name"]),
                case_master_ids=[int(c) for c in record.get("case_ids", [])],
                crime_nos=[str(n) for n in identity.get("crime_nos", [])],
            )
            evidence_items.append(item)
            claims.append(claim(
                f"{record['canonical_name']} — {record['case_count']} linked FIR(s) across "
                f"{record['distinct_crime_heads']} offence type(s); history score "
                f"{float(record['score']):.0f}/100 ({record['band']}).",
                [item], provenance=Provenance.DETERMINISTIC_COMPUTATION,
            ))
        if claims and not claims[0].evidence_locators and evidence_items:
            claims[0].evidence_locators = [evidence_items[0].locator]

        components = records[0].get("components", {}).get("items", []) if single else []
        if single and components:
            for component in components:
                claims.append(claim(
                    f"· {component['name']}: {component['value']} → {component['weight']} points "
                    f"({component['rationale']})",
                    [evidence_items[0]], provenance=Provenance.DETERMINISTIC_COMPUTATION,
                ))
        claims.append(claim(
            "This score summarises what is already recorded — case count, offence variety, recency, and link "
            "position. It is not a prediction about future behaviour and must not be used as one."
        ))

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=evidence_items,
            traces=[trace(
                "offender_scoring",
                "Summed fixed, published weights over recorded case count, distinct offence types, recency of "
                "the most recent case, gravity escalation and degree centrality.",
                inputs={"records": len(records)},
                formula="score = Σ component weights, capped at 100",
                components=components,
                row_count=len(records),
            )],
            payload=StructuredPayload(
                payload_type="score" if single else "table",
                title="Recorded offence history",
                data=(
                    {
                        "subject": records[0]["canonical_name"],
                        "score": records[0]["score"],
                        "band": records[0]["band"],
                        "components": components,
                        "max": 100,
                    }
                    if single else {
                        "columns": ["Person", "Cases", "Offence types", "Score", "Band"],
                        "rows": [
                            [r["canonical_name"], str(r["case_count"]), str(r["distinct_crime_heads"]),
                             f"{float(r['score']):.0f}", str(r["band"])]
                            for r in records
                        ],
                    }
                ),
            ),
            data={"identity_ids": [r["identity_id"] for r in records],
                  "case_master_ids": [int(c) for r in records for c in r.get("case_ids", [])][:200]},
        )

    # --------------------------------------------------------- financial
    def _money_meets_crime(
        self, identity: dict[str, Any] | None, transactions: Sequence[dict[str, Any]], scope: UnitScope,
    ) -> list[dict[str, Any]]:
        """Counterparties who are *also* linked to the subject in the crime graph.

        Two records pointing at the same pair of people — one a transfer, one a
        shared FIR — is a stronger prompt than either alone, and it is the join
        the two datasets exist to support.

        Every result here is marked inferred. The person node on the money side
        is reached through entity resolution, so the correlation inherits that
        resolution's uncertainty: if the identity was assembled from an
        auto-linked pair, so was this.
        """
        if identity is None:
            return []
        seed = person_node(str(identity["identity_id"]))
        expansion = self._graph.expand(seed, scope, hops=1)

        # Everything the crime graph says about this person, money aside.
        crime_links: dict[str, set[str]] = defaultdict(set)
        for link in expansion.view.links:
            if str(link.edge_type) == str(EdgeType.MONEY_FLOW):
                continue
            for node in (link.source, link.target):
                if node != seed and node.startswith("person:"):
                    crime_links[node].add(str(link.edge_type))

        if not crime_links:
            return []

        own_refs = {str(a) for a in identity.get("source_ids", [])}
        joins: dict[str, dict[str, Any]] = {}
        for txn in transactions:
            for ref_key, label_key, kind_key in (
                ("from_ref", "from_label", "from_kind"), ("to_ref", "to_label", "to_kind"),
            ):
                ref = str(txn[ref_key])
                if ref in own_refs or str(txn[kind_key]) != "accused":
                    continue
                try:
                    counterpart = self._identities.identity_for_accused(int(ref))
                except (TypeError, ValueError):
                    continue
                if counterpart is None:
                    continue
                node = person_node(str(counterpart["identity_id"]))
                if node not in crime_links:
                    continue
                entry = joins.setdefault(node, {
                    "label": str(txn[label_key]),
                    "identity_id": str(counterpart["identity_id"]),
                    "crime_edge_types": sorted(crime_links[node]),
                    "txn_ids": [],
                    "amount": 0.0,
                })
                entry["txn_ids"].append(str(txn["txn_id"]))
                entry["amount"] = round(entry["amount"] + float(txn["amount"]), 2)

        results = sorted(joins.values(), key=lambda j: len(j["txn_ids"]), reverse=True)
        return results[:5]

    def _financial_overview(self, request: AgentRequest) -> AgentResult:
        """Suspicious shapes across the whole synthetic transaction extension.

        The entry point when no person or FIR was named. It surfaces the same
        structural observations the per-subject view uses — concentration
        (fan-in/out), onward hop chains, per-account bursts — but ranked across
        everything, so an investigator with no lead yet gets a starting list.
        Every statement stays an arithmetic observation about recorded amounts,
        never a finding of wrongdoing (ADR-0005).
        """
        transactions = self._financial.all_transactions()
        if not transactions:
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(
                    "No transactions are recorded in the financial extension. The FIR schema itself "
                    "holds no financial data.",
                    provenance=Provenance.SYNTHETIC_EXTENSION,
                )],
                warnings=["Financial data is a synthetic extension, not source FIR data."],
            )

        concentrations = self._analyzer.concentration(transactions)
        chains = self._analyzer.hop_chains(transactions)
        bursts = self._analyzer.temporal_bursts(transactions)

        evidence_items = [transaction_evidence(
            txn_id=str(transactions[0]["txn_id"]),
            label=f"{len(transactions)} transaction(s) across the synthetic financial extension",
            case_master_ids=sorted({int(t["case_master_id"]) for t in transactions if t.get("case_master_id")})[:200],
            detail={"transactions": len(transactions), "is_extension": True},
        )]

        claims = [claim(
            f"{len(transactions)} transaction(s) are recorded in the extension. The most notable "
            "structural patterns across all of them are below — arithmetic observations, not "
            "findings of wrongdoing.",
            evidence_items, provenance=Provenance.SYNTHETIC_EXTENSION,
        )]
        for spot in concentrations[:3]:
            direction = "collected from" if spot.direction == "fan-in" else "paid out to"
            claims.append(claim(
                f"{spot.label} ({spot.kind}) {direction} {spot.counterparty_count} distinct "
                f"counterparties totalling ₹{spot.total_amount:,.0f} — above the "
                f"{spot.percentile:.0f}th percentile for this dataset. A position in the recorded "
                "transfers, not a statement about any person.",
                evidence_items, provenance=Provenance.SYNTHETIC_EXTENSION,
            ))
        for chain in chains[:2]:
            claims.append(claim(
                f"Money moved onward through {chain.hops} transfer(s) between {chain.start_date} and "
                f"{chain.end_date}: {' → '.join(chain.path)}. Onward movement is a shape in the "
                "records, not evidence of intent to obscure.",
                evidence_items, provenance=Provenance.SYNTHETIC_EXTENSION,
            ))
        for b in bursts[:2]:
            claims.append(claim(
                f"{b.label} recorded {b.txn_count} transfer(s) on {b.day}, against a "
                f"{b.baseline_days}-day average of {b.baseline_mean:.2f}/day (z = {b.z_score:.1f}). "
                "A prompt to look, not a conclusion.",
                evidence_items, provenance=Provenance.SYNTHETIC_EXTENSION,
            ))
        claims.append(claim(
            "Ask about a named accused or an FIR to trace one subject's money trail in full. These "
            "figures come from a clearly marked synthetic extension, not real banking data."
        ))

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=evidence_items,
            traces=[trace(
                "financial_overview",
                "Ranked concentration, onward hop chains and per-account bursts across every "
                "ext_financial_transaction row. Synthetic extension, not source FIR data.",
                inputs={"transactions": len(transactions),
                        "concentrations": len(concentrations), "chains": len(chains),
                        "bursts": len(bursts)},
                row_count=len(transactions),
            )],
            payload=StructuredPayload(
                payload_type="table",
                title="Suspicious transfer patterns — all accounts (synthetic extension)",
                data={
                    "columns": ["Account", "Direction", "Counterparties", "Total"],
                    "rows": [
                        [c.label, c.direction, str(c.counterparty_count), f"{c.total_amount:,.0f}"]
                        for c in concentrations[:10]
                    ],
                    "is_extension": True,
                },
            ),
            warnings=["Financial data is a synthetic extension, not source FIR data."],
        )

    def _financial_links(self, request: AgentRequest) -> AgentResult:
        request.principal.require(Permission.USE_FINANCIAL_TOOLS)
        names = request.slots.person_names or request.pinned_person_names
        case_ids = request.slots.case_master_ids or request.pinned_case_master_ids
        if request.slots.crime_nos:
            summary = self._cases.by_crime_no(request.slots.crime_nos[0], request.scope)
            if summary:
                case_ids = [summary.case_master_id]

        transactions: list[dict[str, Any]] = []
        subject_ref = subject_label = ""
        subject_refs: list[str] = []
        subject_identity: dict[str, Any] | None = None
        if names:
            identity = self._resolve_identity(names[0])
            if identity is None:
                return self.empty_result(request, f"'{names[0]}' is not present in the indexed accused records.")
            subject_identity = identity
            refs = [str(a) for a in identity.get("source_ids", [])]
            transactions = self._financial.for_refs(refs)
            # Every row this identity resolves to, so a transfer recorded
            # against any of them counts toward the person's totals.
            subject_refs = refs
            subject_ref = refs[0] if refs else ""
            subject_label = str(identity["canonical_name"])
        elif case_ids:
            allowed = [c.case_master_id for c in self._cases.by_ids(case_ids, request.scope)]
            transactions = self._financial.for_cases(allowed)
            subject_label = f"FIR case id(s) {allowed}"
            subject_ref = str(transactions[0]["from_ref"]) if transactions else ""
            subject_refs = [subject_ref] if subject_ref else []
        else:
            # No subject named — a generic "trace money transfers" should show
            # the suspicious *shapes* across the whole synthetic extension, not
            # ask the officer to already know whom to look at.
            return self._financial_overview(request)

        if not transactions:
            return AgentResult(
                agent=self.name, intent=request.intent,
                summary_claims=[claim(
                    f"No transaction is recorded against {subject_label} in the financial extension. "
                    "Note that the FIR schema itself holds no financial data.",
                    provenance=Provenance.SYNTHETIC_EXTENSION,
                )],
                traces=[trace("financial_analysis",
                              "Queried ext_financial_transaction, a synthetic extension table that is not part "
                              "of the organiser's FIR schema.",
                              inputs={"subject": subject_label}, row_count=0)],
                confidence=0.85,
                warnings=["Financial data is a synthetic extension, not source FIR data."],
            )

        # Structural analyses need the neighbourhood; totals stay the subject's own.
        network_transactions = self._financial.neighbourhood(
            sorted({str(t["from_ref"]) for t in transactions} | {str(t["to_ref"]) for t in transactions})
        )
        summary = self._analyzer.summarize(
            subject_ref=subject_ref, subject_label=subject_label, transactions=transactions,
            network_transactions=network_transactions, subject_refs=subject_refs,
        )
        evidence_items = [
            transaction_evidence(
                txn_id=str(transactions[0]["txn_id"]),
                label=f"{len(transactions)} transaction(s) recorded against {subject_label}",
                case_master_ids=summary.case_ids,
                detail={"txn_ids": [str(t["txn_id"]) for t in transactions[:50]],
                        "is_extension": True},
            )
        ]
        claims = [
            claim(
                f"{len(transactions)} transaction(s) involve {subject_label}: ₹{summary.total_received:,.0f} "
                f"received and ₹{summary.total_sent:,.0f} sent.",
                evidence_items, provenance=Provenance.SYNTHETIC_EXTENSION,
            )
        ]
        for flow in summary.counterparties[:5]:
            claims.append(claim(
                f"{flow.label} ({flow.kind}): {flow.txn_count} transaction(s), net "
                f"₹{flow.net:,.0f}.",
                evidence_items, provenance=Provenance.SYNTHETIC_EXTENSION,
            ))
        for pattern in summary.patterns[:3]:
            claims.append(claim(
                f"{pattern['pattern'].capitalize()} — {pattern['observation']}. {pattern['caveat']}",
                evidence_items, provenance=Provenance.SYNTHETIC_EXTENSION,
            ))

        for chain in summary.chains[:2]:
            claims.append(claim(
                f"Money moved onward through {chain.hops} transfer(s) between {chain.start_date} and "
                f"{chain.end_date}: {' → '.join(chain.path)}. "
                f"₹{chain.amounts[0]:,.0f} at the first hop, ₹{chain.amounts[-1]:,.0f} at the last. "
                "Onward movement is a shape in the recorded transfers; it is not itself evidence of "
                "an attempt to obscure the origin.",
                evidence_items, provenance=Provenance.SYNTHETIC_EXTENSION,
            ))

        for spot in summary.concentrations[:2]:
            direction = "collected from" if spot.direction == "fan-in" else "paid out to"
            claims.append(claim(
                f"{spot.label} ({spot.kind}) {direction} {spot.counterparty_count} distinct counterparties "
                f"across {spot.txn_count} transfer(s) totalling ₹{spot.total_amount:,.0f} — at or above the "
                f"{spot.percentile:.0f}th percentile of this dataset, which is "
                f"{spot.threshold_degree:.0f} counterparties. This describes the account's position in the "
                "recorded transfers, not the conduct of any person.",
                evidence_items, provenance=Provenance.SYNTHETIC_EXTENSION,
            ))

        for burst in summary.bursts[:2]:
            claims.append(claim(
                f"{burst.label} recorded {burst.txn_count} transfer(s) on {burst.day}, against a "
                f"{burst.baseline_days}-day average of {burst.baseline_mean:.2f} per day for that same "
                f"account (z = {burst.z_score:.1f}). A single busy day has many ordinary explanations; "
                "this is a prompt to look, not a conclusion.",
                evidence_items, provenance=Provenance.SYNTHETIC_EXTENSION,
            ))

        for position in summary.positions[:2]:
            if position.betweenness <= 0:
                continue
            claims.append(claim(
                f"{position.label} sits between other parties on {position.betweenness:.3f} of the shortest "
                f"routes through the recorded transfers, with {position.degree} direct counterparties. "
                "This is a structural position in the money-flow graph, not a statement about culpability.",
                evidence_items, provenance=Provenance.SYNTHETIC_EXTENSION,
            ))

        bands = [b for b in summary.amount_bands if b.count]
        if bands:
            breakdown = "; ".join(f"{b.label}: {b.count}" for b in bands)
            claims.append(claim(
                f"Across {len(transactions)} transfer(s) the amounts fall as — {breakdown}. "
                "The distribution is given in full so a cluster near the threshold can be read in context.",
                evidence_items, provenance=Provenance.SYNTHETIC_EXTENSION,
            ))

        joins = self._money_meets_crime(subject_identity, transactions, request.scope)
        for join in joins:
            phrasing = ", ".join(
                EDGE_PHRASING.get(edge_type, edge_type.lower().replace("_", " "))
                for edge_type in join["crime_edge_types"]
            )
            claims.append(claim(
                f"{join['label']} appears on both sides of the record: {len(join['txn_ids'])} transfer(s) "
                f"totalling ₹{join['amount']:,.0f} with {subject_label}, and a link in the case graph "
                f"({phrasing}). The person on the money side is matched through entity resolution, so this "
                "correlation carries that matching's uncertainty.",
                evidence_items, provenance=Provenance.INFERRED,
            ))

        claims.append(claim(
            "These transactions come from a clearly marked synthetic extension table. The organiser's FIR "
            "schema contains no financial records, so nothing here reflects real banking data."
        ))

        return AgentResult(
            agent=self.name, intent=request.intent, summary_claims=claims, evidence=evidence_items,
            traces=[trace(
                "financial_analysis",
                "Aggregated ext_financial_transaction rows by counterparty, then ran six deterministic "
                "observations over them: near-threshold amounts, same-day volume, onward hop chains, "
                "counterparty concentration, per-account daily bursts, and money-flow network position. "
                "This table is a synthetic extension, not source FIR data.",
                inputs={
                    "subject": subject_label,
                    "transactions": len(transactions),
                    "chain_window_days": CHAIN_WINDOW_DAYS,
                    "concentration_percentile": CONCENTRATION_PERCENTILE,
                    "min_accounts_for_concentration": MIN_ACCOUNTS_FOR_CONCENTRATION,
                    "burst_baseline_days": BURST_BASELINE_DAYS,
                    "burst_z_threshold": BURST_Z_THRESHOLD,
                    "reporting_threshold_inr": STRUCTURING_THRESHOLD,
                },
                formula=(
                    "net = Σ received − Σ sent per counterparty; "
                    "concentration cutoff = nearest-rank p90 of the observed degree distribution; "
                    "burst z = (day count − mean(prior 30 calendar days)) / max(stddev, 1.0); "
                    "betweenness = fraction of shortest paths through the node"
                ),
                row_count=len(transactions),
            )],
            payload=StructuredPayload(
                payload_type="table", title=f"Recorded transfers — {subject_label} (synthetic extension)",
                data={
                    "columns": ["Counterparty", "Kind", "Transactions", "Received", "Sent", "Net"],
                    "rows": [
                        [f.label, f.kind, str(f.txn_count), f"{f.received:,.0f}", f"{f.sent:,.0f}", f"{f.net:,.0f}"]
                        for f in summary.counterparties
                    ],
                    "is_extension": True,
                },
            ),
            warnings=["Financial data is a synthetic extension, not source FIR data."],
            data={"case_master_ids": summary.case_ids,
                  "txn_ids": [str(t["txn_id"]) for t in transactions[:100]]},
        )

    # --------------------------------------------------------------- utils
    def _graph_trace(self, expansion: Any, hops: int) -> Any:
        return trace(
            "graph_expansion",
            f"Expanded the link graph {hops} hop(s) from {expansion.seed_node}, keeping only edges whose "
            "originating police station falls inside your authorized scope, then computed degree centrality "
            "and Louvain communities over the resulting subgraph.",
            inputs={"seed": expansion.seed_node, "hops": hops,
                    "edge_types": expansion.edge_type_counts,
                    "withheld_by_scope": expansion.trimmed_by_scope},
            row_count=len(expansion.view.links),
        )

    def _graph_payload(self, title: str, view: Any, seed: str) -> StructuredPayload:
        return StructuredPayload(
            payload_type="graph",
            title=title,
            data={
                "seed": seed,
                "nodes": [
                    {
                        "id": node.node_id, "label": node.label, "type": str(node.node_type),
                        "community": view.communities.get(node.node_id, 0),
                        "centrality": view.centrality.get(node.node_id, 0.0),
                        "seed": node.node_id == seed,
                    }
                    for node in view.nodes
                ],
                "links": [
                    {
                        "source": link.source, "target": link.target, "type": str(link.edge_type),
                        "weight": link.weight, "case_master_ids": link.case_master_ids[:10],
                        "inferred": link.provenance is Provenance.INFERRED,
                        "basis": EDGE_PHRASING.get(str(link.edge_type), str(link.edge_type)),
                    }
                    for link in view.links
                ],
            },
        )
