"""Graph construction from curated data.

The production architecture puts this in Neo4j (§8.2). At hackathon scale the
same model is materialised as rows in ``cip_graph_edge`` and traversed with
NetworkX in memory — a scale-appropriate substitution with an explicit upgrade
path, not a different design. Node ids and edge types match the Neo4j model
one-for-one so the Cypher migration is mechanical.

Node id grammar (stable, used across the API and the console):
    ``person:<identity_id>`` ``case:<case_master_id>`` ``officer:<employee_id>``
    ``location:<grid_row>:<grid_col>`` ``station:<unit_id>`` ``entity:<ref>``

**Every edge here is inferred.** Edges are derived, not read from the source,
so each carries ``provenance = inferred`` and the case ids that produced it.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable, Sequence

from ...domain.enums import EdgeType, NodeType, Provenance
from ...domain.value_objects import GeoPoint
from .entity_resolution import AccusedRecord, Identity

#: Person→Case membership edge. It restates a source record rather than
#: inferring anything, so it carries SOURCE_RECORD provenance.
ALLEGED_IN = "ALLEGED_IN"

MODUS_OPERANDI_WINDOW_DAYS = 21
MODUS_OPERANDI_RADIUS_METRES = 6_000
LOCATION_GRID_METRES = 1_000


def person_node(identity_id: str) -> str:
    return f"person:{identity_id}"


def case_node(case_master_id: int) -> str:
    return f"case:{case_master_id}"


def officer_node(employee_id: int) -> str:
    return f"officer:{employee_id}"


def location_node(row: int, col: int) -> str:
    return f"location:{row}:{col}"


def entity_node(ref: str) -> str:
    return f"entity:{ref}"


def _edge_id(*parts: Any) -> str:
    return hashlib.blake2s(":".join(str(p) for p in parts).encode("utf-8"), digest_size=10).hexdigest()


def _edge(
    *,
    src_type: NodeType,
    src_id: str,
    dst_type: NodeType,
    dst_id: str,
    edge_type: EdgeType,
    weight: float,
    case_ids: Sequence[int],
    unit_ids: Sequence[int] = (),
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "edge_id": _edge_id(edge_type, src_id, dst_id),
        "src_type": str(src_type),
        "src_id": src_id,
        "dst_type": str(dst_type),
        "dst_id": dst_id,
        "edge_type": str(edge_type),
        "weight": round(float(weight), 4),
        "case_ids": sorted(set(int(c) for c in case_ids)),
        "unit_ids": sorted(set(int(u) for u in unit_ids if u is not None)),
        "provenance": str(Provenance.INFERRED),
        "detail": detail or {},
    }


class GraphBuilder:
    """Pure function object: curated rows in, edge dictionaries out."""

    def build(
        self,
        *,
        identities: Sequence[Identity],
        accused_records: Sequence[AccusedRecord],
        cases: Sequence[dict[str, Any]],
        arrests: Sequence[dict[str, Any]],
        transactions: Sequence[dict[str, Any]] = (),
    ) -> list[dict[str, Any]]:
        identity_for_accused = {
            accused_id: identity for identity in identities for accused_id in identity.source_ids
        }
        case_index = {int(row["CaseMasterID"]): row for row in cases}
        edges: list[dict[str, Any]] = []

        edges.extend(self._co_accused(accused_records, identity_for_accused, case_index))
        edges.extend(self._alleged_in(identities, case_index))
        edges.extend(self._repeat_offender(identities))
        edges.extend(self._same_location(cases))
        edges.extend(self._same_modus_operandi(cases))
        edges.extend(self._arrested_by(arrests, identity_for_accused, case_index))
        edges.extend(self._money_flow(transactions, identity_for_accused))

        deduped: dict[str, dict[str, Any]] = {}
        for edge in edges:
            existing = deduped.get(edge["edge_id"])
            if existing is None:
                deduped[edge["edge_id"]] = edge
            else:
                existing["weight"] = round(existing["weight"] + edge["weight"], 4)
                existing["case_ids"] = sorted(set(existing["case_ids"]) | set(edge["case_ids"]))
        return list(deduped.values())

    # ---------------------------------------------------------- edge types
    def _co_accused(
        self,
        records: Sequence[AccusedRecord],
        identity_for_accused: dict[int, Identity],
        case_index: dict[int, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        by_case: dict[int, list[AccusedRecord]] = defaultdict(list)
        for record in records:
            by_case[record.case_master_id].append(record)
        edges: list[dict[str, Any]] = []
        for case_id, members in by_case.items():
            identity_ids = sorted({
                identity_for_accused[m.accused_master_id].identity_id
                for m in members
                if m.accused_master_id in identity_for_accused
            })
            unit_id = case_index.get(case_id, {}).get("PoliceStationID")
            for i, left in enumerate(identity_ids):
                for right in identity_ids[i + 1:]:
                    edges.append(
                        _edge(
                            src_type=NodeType.PERSON, src_id=person_node(left),
                            dst_type=NodeType.PERSON, dst_id=person_node(right),
                            edge_type=EdgeType.CO_ACCUSED, weight=1.0,
                            case_ids=[case_id], unit_ids=[unit_id] if unit_id else (),
                            detail={"basis": "named as accused in the same FIR"},
                        )
                    )
        return edges

    def _alleged_in(
        self, identities: Sequence[Identity], case_index: dict[int, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        edges: list[dict[str, Any]] = []
        for identity in identities:
            for case_id in identity.case_ids:
                unit_id = case_index.get(case_id, {}).get("PoliceStationID")
                edges.append(
                    {
                        "edge_id": _edge_id(ALLEGED_IN, identity.identity_id, case_id),
                        "src_type": str(NodeType.PERSON),
                        "src_id": person_node(identity.identity_id),
                        "dst_type": str(NodeType.CASE),
                        "dst_id": case_node(case_id),
                        "edge_type": ALLEGED_IN,
                        "weight": 1.0,
                        "case_ids": [case_id],
                        "unit_ids": [int(unit_id)] if unit_id else [],
                        "provenance": str(Provenance.SOURCE_RECORD),
                        "detail": {"basis": "accused record in this FIR", "role": "accused",
                                   "identity_id": identity.identity_id},
                    }
                )
        return edges

    def _repeat_offender(self, identities: Sequence[Identity]) -> list[dict[str, Any]]:
        edges: list[dict[str, Any]] = []
        for identity in identities:
            if len(identity.case_ids) < 2:
                continue
            ordered = sorted(identity.case_ids)
            for left, right in zip(ordered, ordered[1:]):
                edges.append(
                    _edge(
                        src_type=NodeType.CASE, src_id=case_node(left),
                        dst_type=NodeType.CASE, dst_id=case_node(right),
                        edge_type=EdgeType.REPEAT_OFFENDER, weight=1.0,
                        case_ids=[left, right],
                        detail={"basis": "same resolved person named as accused",
                                "identity_id": identity.identity_id,
                                "person": identity.canonical_name},
                    )
                )
        return edges

    def _same_location(self, cases: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        buckets: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
        for row in cases:
            latitude, longitude = row.get("latitude"), row.get("longitude")
            if latitude is None or longitude is None:
                continue
            cell = GeoPoint(float(latitude), float(longitude)).grid_cell(LOCATION_GRID_METRES)
            buckets[cell].append(row)
        edges: list[dict[str, Any]] = []
        for (grid_row, grid_col), members in buckets.items():
            if len(members) < 2:
                continue
            node = location_node(grid_row, grid_col)
            for row in members:
                edges.append(
                    _edge(
                        src_type=NodeType.CASE, src_id=case_node(int(row["CaseMasterID"])),
                        dst_type=NodeType.LOCATION, dst_id=node,
                        edge_type=EdgeType.SAME_LOCATION, weight=1.0,
                        case_ids=[int(row["CaseMasterID"])],
                        unit_ids=[row["PoliceStationID"]] if row.get("PoliceStationID") else (),
                        detail={"basis": f"incident coordinates fall in the same ~{LOCATION_GRID_METRES} m grid cell",
                                "grid_metres": LOCATION_GRID_METRES},
                    )
                )
        return edges

    def _same_modus_operandi(self, cases: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        by_sub_head: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in cases:
            sub_head = row.get("CrimeMinorHeadID")
            registered = row.get("CrimeRegisteredDate")
            if sub_head is None or not registered:
                continue
            by_sub_head[int(sub_head)].append(row)
        edges: list[dict[str, Any]] = []
        for sub_head, members in by_sub_head.items():
            members.sort(key=lambda r: str(r["CrimeRegisteredDate"]))
            for index, left in enumerate(members):
                left_date = date.fromisoformat(str(left["CrimeRegisteredDate"])[:10])
                left_point = _point(left)
                for right in members[index + 1:]:
                    right_date = date.fromisoformat(str(right["CrimeRegisteredDate"])[:10])
                    if right_date - left_date > timedelta(days=MODUS_OPERANDI_WINDOW_DAYS):
                        break
                    right_point = _point(right)
                    if left_point is None or right_point is None:
                        continue
                    distance = left_point.distance_metres(right_point)
                    if distance > MODUS_OPERANDI_RADIUS_METRES:
                        continue
                    weight = round(1.0 - distance / MODUS_OPERANDI_RADIUS_METRES, 4)
                    edges.append(
                        _edge(
                            src_type=NodeType.CASE, src_id=case_node(int(left["CaseMasterID"])),
                            dst_type=NodeType.CASE, dst_id=case_node(int(right["CaseMasterID"])),
                            edge_type=EdgeType.SAME_MODUS_OPERANDI, weight=max(weight, 0.05),
                            case_ids=[int(left["CaseMasterID"]), int(right["CaseMasterID"])],
                            detail={
                                "basis": "same crime sub-head within a tight time and distance window",
                                "crime_sub_head_id": sub_head,
                                "days_apart": (right_date - left_date).days,
                                "metres_apart": int(distance),
                                "window_days": MODUS_OPERANDI_WINDOW_DAYS,
                                "radius_metres": MODUS_OPERANDI_RADIUS_METRES,
                            },
                        )
                    )
        return edges

    def _arrested_by(
        self,
        arrests: Sequence[dict[str, Any]],
        identity_for_accused: dict[int, Identity],
        case_index: dict[int, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        edges: list[dict[str, Any]] = []
        for row in arrests:
            accused_id = row.get("AccusedMasterID")
            officer_id = row.get("IOID")
            if not accused_id or not officer_id:
                continue
            identity = identity_for_accused.get(int(accused_id))
            if identity is None:
                continue
            case_id = int(row["CaseMasterID"])
            edges.append(
                _edge(
                    src_type=NodeType.PERSON, src_id=person_node(identity.identity_id),
                    dst_type=NodeType.OFFICER, dst_id=officer_node(int(officer_id)),
                    edge_type=EdgeType.ARRESTED_BY, weight=1.0, case_ids=[case_id],
                    unit_ids=[case_index.get(case_id, {}).get("PoliceStationID")] if case_index.get(case_id) else (),
                    detail={"basis": "arrest or surrender recorded against this investigating officer",
                            "arrest_id": row.get("ArrestSurrenderID")},
                )
            )
        return edges

    def _money_flow(
        self, transactions: Sequence[dict[str, Any]], identity_for_accused: dict[int, Identity]
    ) -> list[dict[str, Any]]:
        edges: list[dict[str, Any]] = []
        for row in transactions:
            source = _financial_node(row["from_kind"], row["from_ref"], identity_for_accused)
            target = _financial_node(row["to_kind"], row["to_ref"], identity_for_accused)
            if source is None or target is None:
                continue
            case_id = row.get("case_master_id")
            edges.append(
                _edge(
                    src_type=source[0], src_id=source[1], dst_type=target[0], dst_id=target[1],
                    edge_type=EdgeType.MONEY_FLOW, weight=float(row["amount"]),
                    case_ids=[int(case_id)] if case_id else [],
                    detail={
                        "basis": "synthetic financial extension — not present in the source FIR schema",
                        "is_extension": True,
                        "txn_id": row["txn_id"],
                        "amount": float(row["amount"]),
                        "channel": row.get("channel"),
                        "txn_date": row.get("txn_date"),
                    },
                )
            )
        return edges


def _point(row: dict[str, Any]) -> GeoPoint | None:
    latitude, longitude = row.get("latitude"), row.get("longitude")
    if latitude is None or longitude is None:
        return None
    return GeoPoint(float(latitude), float(longitude))


def _financial_node(
    kind: str, ref: str, identity_for_accused: dict[int, Identity]
) -> tuple[NodeType, str] | None:
    if kind == "accused":
        try:
            identity = identity_for_accused.get(int(ref))
        except (TypeError, ValueError):
            return None
        if identity is None:
            return None
        return NodeType.PERSON, person_node(identity.identity_id)
    return NodeType.ENTITY, entity_node(str(ref))
