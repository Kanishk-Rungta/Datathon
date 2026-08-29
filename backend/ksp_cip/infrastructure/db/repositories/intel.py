"""Repositories for derived intelligence: graph edges, vectors, scores,
alerts, entity resolution and the synthetic financial extension.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from ....domain.ports import DataStore
from .cases import in_clause


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GraphRepository:
    def __init__(self, store: DataStore) -> None:
        self._store = store

    def replace_all(self, edges: Sequence[dict[str, Any]]) -> int:
        self._store.execute("DELETE FROM cip_graph_edge")
        return self.upsert_many(edges)

    def upsert_many(self, edges: Sequence[dict[str, Any]]) -> int:
        if not edges:
            return 0
        rows = [
            {
                "edge_id": edge["edge_id"],
                "src_type": edge["src_type"],
                "src_id": edge["src_id"],
                "dst_type": edge["dst_type"],
                "dst_id": edge["dst_id"],
                "edge_type": edge["edge_type"],
                "weight": float(edge.get("weight", 1.0)),
                "case_ids": json.dumps(sorted(set(edge.get("case_ids", [])))),
                "unit_ids": json.dumps(sorted(set(edge.get("unit_ids", [])))),
                "provenance": edge.get("provenance", "inferred"),
                "detail_json": json.dumps(edge.get("detail", {}), ensure_ascii=False),
                "created_at": _now(),
            }
            for edge in edges
        ]
        return self._store.execute_many(
            "INSERT INTO cip_graph_edge (edge_id, src_type, src_id, dst_type, dst_id, edge_type, weight,"
            " case_ids, unit_ids, provenance, detail_json, created_at)"
            " VALUES (:edge_id, :src_type, :src_id, :dst_type, :dst_id, :edge_type, :weight,"
            " :case_ids, :unit_ids, :provenance, :detail_json, :created_at)"
            " ON CONFLICT (edge_id) DO UPDATE SET weight = excluded.weight,"
            " case_ids = excluded.case_ids, unit_ids = excluded.unit_ids,"
            " detail_json = excluded.detail_json, created_at = excluded.created_at",
            rows,
        )

    def all_edges(self, edge_types: Sequence[str] | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM cip_graph_edge"
        params: dict[str, Any] = {}
        if edge_types:
            fragment, params = in_clause("et", list(edge_types))
            sql += f" WHERE edge_type IN ({fragment})"
        return [_decode_edge(row) for row in self._store.query(sql, params)]

    def edges_for_node(self, node_type: str, node_id: str) -> list[dict[str, Any]]:
        rows = self._store.query(
            "SELECT * FROM cip_graph_edge WHERE (src_type = :t AND src_id = :i)"
            " OR (dst_type = :t AND dst_id = :i)",
            {"t": node_type, "i": node_id},
        )
        return [_decode_edge(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        rows = self._store.query(
            "SELECT edge_type, COUNT(*) AS n, AVG(weight) AS avg_weight FROM cip_graph_edge GROUP BY edge_type"
        )
        total = sum(int(r["n"]) for r in rows)
        return {"total_edges": total, "by_type": rows}


def _decode_edge(row: dict[str, Any]) -> dict[str, Any]:
    decoded = dict(row)
    decoded["case_ids"] = json.loads(row.get("case_ids") or "[]")
    decoded["unit_ids"] = json.loads(row.get("unit_ids") or "[]")
    decoded["detail"] = json.loads(row.get("detail_json") or "{}")
    return decoded


class EmbeddingRepository:
    def __init__(self, store: DataStore) -> None:
        self._store = store

    def replace_all(self, docs: Sequence[dict[str, Any]], model_name: str) -> int:
        self._store.execute("DELETE FROM cip_embedding_index WHERE model_name = :m", {"m": model_name})
        if not docs:
            return 0
        rows = [
            {
                "doc_id": doc["doc_id"],
                "source_table": doc["source_table"],
                "source_pk": str(doc["source_pk"]),
                "case_master_id": doc.get("case_master_id"),
                "unit_id": doc.get("unit_id"),
                "lang": doc.get("lang", "en"),
                "text_snippet": doc["text_snippet"],
                "embedding": json.dumps([round(v, 6) for v in doc["embedding"]]),
                "model_name": model_name,
                "created_at": _now(),
            }
            for doc in docs
        ]
        return self._store.execute_many(
            "INSERT OR REPLACE INTO cip_embedding_index (doc_id, source_table, source_pk, case_master_id,"
            " unit_id, lang, text_snippet, embedding, model_name, created_at)"
            " VALUES (:doc_id, :source_table, :source_pk, :case_master_id, :unit_id, :lang,"
            " :text_snippet, :embedding, :model_name, :created_at)",
            rows,
        )

    def load_all(self, model_name: str) -> list[dict[str, Any]]:
        rows = self._store.query(
            "SELECT doc_id, source_table, source_pk, case_master_id, unit_id, lang, text_snippet, embedding"
            " FROM cip_embedding_index WHERE model_name = :m ORDER BY doc_id",
            {"m": model_name},
        )
        for row in rows:
            row["embedding"] = json.loads(row["embedding"])
        return rows

    def count(self, model_name: str) -> int:
        rows = self._store.query(
            "SELECT COUNT(*) AS n FROM cip_embedding_index WHERE model_name = :m", {"m": model_name}
        )
        return int(rows[0]["n"]) if rows else 0

    def save_model_state(self, state: dict[str, Any]) -> None:
        self._store.execute(
            "INSERT INTO cip_embedding_stats (model_name, dimensions, doc_count, idf_json, built_at)"
            " VALUES (:m, :d, :c, :idf, :t)"
            " ON CONFLICT (model_name) DO UPDATE SET dimensions = excluded.dimensions,"
            " doc_count = excluded.doc_count, idf_json = excluded.idf_json, built_at = excluded.built_at",
            {
                "m": state["model_name"],
                "d": state["dimensions"],
                "c": state["doc_count"],
                "idf": json.dumps(state["idf"]),
                "t": _now(),
            },
        )

    def load_model_state(self, model_name: str) -> dict[str, Any] | None:
        rows = self._store.query(
            "SELECT model_name, dimensions, doc_count, idf_json FROM cip_embedding_stats WHERE model_name = :m",
            {"m": model_name},
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "model_name": row["model_name"],
            "dimensions": row["dimensions"],
            "doc_count": row["doc_count"],
            "idf": json.loads(row["idf_json"]),
        }


class IdentityRepository:
    """Entity-resolution outputs: identities, candidate links, offender scores."""

    def __init__(self, store: DataStore) -> None:
        self._store = store

    def replace_identities(self, identities: Sequence[dict[str, Any]]) -> int:
        self._store.execute("DELETE FROM cip_person_identity")
        if not identities:
            return 0
        rows = [
            {
                "identity_id": item["identity_id"],
                "canonical_name": item["canonical_name"],
                "normalized_name": item["normalized_name"],
                "phonetic_key": item["phonetic_key"],
                "age_estimate": item.get("age_estimate"),
                "gender_id": item.get("gender_id"),
                "district_ids": json.dumps(sorted(set(item.get("district_ids", [])))),
                "source_ids": json.dumps(sorted(set(item.get("source_ids", [])))),
                "case_ids": json.dumps(sorted(set(item.get("case_ids", [])))),
                "member_count": len(item.get("source_ids", [])) or 1,
                "created_at": _now(),
            }
            for item in identities
        ]
        return self._store.execute_many(
            "INSERT OR REPLACE INTO cip_person_identity (identity_id, canonical_name, normalized_name,"
            " phonetic_key, age_estimate, gender_id, district_ids, source_ids, case_ids, member_count, created_at)"
            " VALUES (:identity_id, :canonical_name, :normalized_name, :phonetic_key, :age_estimate,"
            " :gender_id, :district_ids, :source_ids, :case_ids, :member_count, :created_at)",
            rows,
        )

    def identities(self) -> list[dict[str, Any]]:
        rows = self._store.query("SELECT * FROM cip_person_identity ORDER BY member_count DESC")
        return [_decode_identity(row) for row in rows]

    def identity(self, identity_id: str) -> dict[str, Any] | None:
        rows = self._store.query(
            "SELECT * FROM cip_person_identity WHERE identity_id = :i", {"i": identity_id}
        )
        return _decode_identity(rows[0]) if rows else None

    def identity_for_accused(self, accused_master_id: int) -> dict[str, Any] | None:
        for identity in self.identities():
            if accused_master_id in identity["source_ids"]:
                return identity
        return None

    def replace_links(self, links: Sequence[dict[str, Any]]) -> int:
        self._store.execute("DELETE FROM cip_entity_resolution_link WHERE review_state = 'pending'")
        if not links:
            return 0
        rows = [
            {
                "link_id": link["link_id"],
                "left_accused_id": link["left_accused_id"],
                "right_accused_id": link["right_accused_id"],
                "score": float(link["score"]),
                "decision": link["decision"],
                "review_state": link.get("review_state", "pending"),
                "features_json": json.dumps(link["features"], ensure_ascii=False),
                "created_at": _now(),
            }
            for link in links
        ]
        return self._store.execute_many(
            "INSERT OR REPLACE INTO cip_entity_resolution_link (link_id, left_accused_id, right_accused_id,"
            " score, decision, review_state, features_json, created_at)"
            " VALUES (:link_id, :left_accused_id, :right_accused_id, :score, :decision, :review_state,"
            " :features_json, :created_at)",
            rows,
        )

    def review_queue(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._store.query(
            "SELECT l.*, la.AccusedName AS left_name, ra.AccusedName AS right_name"
            " FROM cip_entity_resolution_link l"
            " LEFT JOIN curated_Accused la ON la.AccusedMasterID = l.left_accused_id"
            " LEFT JOIN curated_Accused ra ON ra.AccusedMasterID = l.right_accused_id"
            " WHERE l.decision = 'review' AND l.review_state = 'pending'"
            " ORDER BY l.score DESC LIMIT :limit",
            {"limit": limit},
        )
        for row in rows:
            row["features"] = json.loads(row.pop("features_json"))
        return rows

    def resolve_link(self, link_id: str, *, state: str, reviewer: str) -> None:
        self._store.execute(
            "UPDATE cip_entity_resolution_link SET review_state = :s, reviewed_by = :r, reviewed_at = :t"
            " WHERE link_id = :i",
            {"s": state, "r": reviewer, "t": _now(), "i": link_id},
        )

    def link_stats(self) -> dict[str, Any]:
        rows = self._store.query(
            "SELECT decision, review_state, COUNT(*) AS n FROM cip_entity_resolution_link"
            " GROUP BY decision, review_state"
        )
        return {"buckets": rows, "total": sum(int(r["n"]) for r in rows)}

    # --------------------------------------------------------------- scores
    def replace_offender_scores(self, scores: Sequence[dict[str, Any]]) -> int:
        self._store.execute("DELETE FROM cip_repeat_offender_score")
        if not scores:
            return 0
        rows = [
            {
                "identity_id": item["identity_id"],
                "canonical_name": item["canonical_name"],
                "case_count": item["case_count"],
                "distinct_crime_heads": item["distinct_crime_heads"],
                "recency_days": item.get("recency_days"),
                "gravity_escalation": item.get("gravity_escalation", 0.0),
                "network_centrality": item.get("network_centrality", 0.0),
                "score": item["score"],
                "band": item["band"],
                "components_json": json.dumps(item["components"], ensure_ascii=False),
                "case_ids": json.dumps(sorted(set(item["case_ids"]))),
                "district_ids": json.dumps(sorted(set(item.get("district_ids", [])))),
                "unit_ids": json.dumps(sorted(set(item.get("unit_ids", [])))),
                "computed_at": _now(),
            }
            for item in scores
        ]
        return self._store.execute_many(
            "INSERT OR REPLACE INTO cip_repeat_offender_score (identity_id, canonical_name, case_count,"
            " distinct_crime_heads, recency_days, gravity_escalation, network_centrality, score, band,"
            " components_json, case_ids, district_ids, unit_ids, computed_at)"
            " VALUES (:identity_id, :canonical_name, :case_count, :distinct_crime_heads, :recency_days,"
            " :gravity_escalation, :network_centrality, :score, :band, :components_json, :case_ids,"
            " :district_ids, :unit_ids, :computed_at)",
            rows,
        )

    def top_offenders(self, *, limit: int = 20, unit_ids: Sequence[int] | None = None) -> list[dict[str, Any]]:
        rows = self._store.query(
            "SELECT * FROM cip_repeat_offender_score ORDER BY score DESC LIMIT :limit",
            {"limit": max(limit * 4, limit)},
        )
        decoded = [_decode_offender(row) for row in rows]
        if unit_ids is not None:
            allowed = set(unit_ids)
            decoded = [row for row in decoded if allowed.intersection(row["unit_ids"])]
        return decoded[:limit]

    def offender(self, identity_id: str) -> dict[str, Any] | None:
        rows = self._store.query(
            "SELECT * FROM cip_repeat_offender_score WHERE identity_id = :i", {"i": identity_id}
        )
        return _decode_offender(rows[0]) if rows else None


def _decode_identity(row: dict[str, Any]) -> dict[str, Any]:
    decoded = dict(row)
    for key in ("district_ids", "source_ids", "case_ids"):
        decoded[key] = json.loads(row.get(key) or "[]")
    return decoded


def _decode_offender(row: dict[str, Any]) -> dict[str, Any]:
    decoded = dict(row)
    decoded["components"] = json.loads(row.pop("components_json") or "{}")
    for key in ("case_ids", "district_ids", "unit_ids"):
        decoded[key] = json.loads(row.get(key) or "[]")
    return decoded


class HotspotRepository:
    def __init__(self, store: DataStore) -> None:
        self._store = store

    def replace_cells(self, cells: Sequence[dict[str, Any]]) -> int:
        self._store.execute("DELETE FROM cip_hotspot_cell")
        if not cells:
            return 0
        rows = [
            {**cell, "case_ids": json.dumps(sorted(set(cell["case_ids"]))), "computed_at": _now()}
            for cell in cells
        ]
        return self._store.execute_many(
            "INSERT OR REPLACE INTO cip_hotspot_cell (cell_id, grid_row, grid_col, centroid_lat, centroid_lon,"
            " district_id, unit_id, window_start, window_end, case_count, baseline_mean, intensity,"
            " top_crime_sub_head, case_ids, computed_at)"
            " VALUES (:cell_id, :grid_row, :grid_col, :centroid_lat, :centroid_lon, :district_id, :unit_id,"
            " :window_start, :window_end, :case_count, :baseline_mean, :intensity, :top_crime_sub_head,"
            " :case_ids, :computed_at)",
            rows,
        )

    def cells(self, *, district_ids: Sequence[int] | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM cip_hotspot_cell"
        params: dict[str, Any] = {"limit": limit}
        if district_ids:
            fragment, extra = in_clause("d", list(district_ids))
            sql += f" WHERE district_id IN ({fragment})"
            params.update(extra)
        sql += " ORDER BY intensity DESC, case_count DESC LIMIT :limit"
        rows = self._store.query(sql, params)
        for row in rows:
            row["case_ids"] = json.loads(row["case_ids"])
        return rows


class AlertRepository:
    def __init__(self, store: DataStore) -> None:
        self._store = store

    def replace_alerts(self, alerts: Sequence[dict[str, Any]]) -> int:
        self._store.execute("DELETE FROM cip_early_warning_alert")
        if not alerts:
            return 0
        rows = [
            {
                **alert,
                "case_ids": json.dumps(sorted(set(alert["case_ids"]))),
                "explanation_json": json.dumps(alert["explanation"], ensure_ascii=False),
                "computed_at": _now(),
            }
            for alert in alerts
        ]
        for row in rows:
            row.pop("explanation", None)
        return self._store.execute_many(
            "INSERT OR REPLACE INTO cip_early_warning_alert (alert_id, scope_type, scope_id, scope_name,"
            " crime_sub_head_id, crime_sub_head, window_start, window_end, observed_count, baseline_mean,"
            " baseline_stddev, z_score, severity, case_ids, explanation_json, computed_at)"
            " VALUES (:alert_id, :scope_type, :scope_id, :scope_name, :crime_sub_head_id, :crime_sub_head,"
            " :window_start, :window_end, :observed_count, :baseline_mean, :baseline_stddev, :z_score,"
            " :severity, :case_ids, :explanation_json, :computed_at)",
            rows,
        )

    def alerts(self, *, district_ids: Sequence[int] | None = None, limit: int = 25) -> list[dict[str, Any]]:
        sql = "SELECT * FROM cip_early_warning_alert"
        params: dict[str, Any] = {"limit": limit}
        if district_ids:
            fragment, extra = in_clause("d", list(district_ids))
            sql += f" WHERE (scope_type = 'district' AND scope_id IN ({fragment}))"
            params.update(extra)
        sql += " ORDER BY z_score DESC LIMIT :limit"
        rows = self._store.query(sql, params)
        for row in rows:
            row["case_ids"] = json.loads(row["case_ids"])
            row["explanation"] = json.loads(row.pop("explanation_json"))
        return rows


class PriorityRepository:
    def __init__(self, store: DataStore) -> None:
        self._store = store

    def replace_scores(self, scores: Sequence[dict[str, Any]]) -> int:
        self._store.execute("DELETE FROM cip_case_priority")
        if not scores:
            return 0
        rows = [
            {
                "case_master_id": item["case_master_id"],
                "crime_no": item["crime_no"],
                "score": item["score"],
                "band": item["band"],
                "components_json": json.dumps(item["components"], ensure_ascii=False),
                "computed_at": _now(),
            }
            for item in scores
        ]
        return self._store.execute_many(
            "INSERT OR REPLACE INTO cip_case_priority (case_master_id, crime_no, score, band,"
            " components_json, computed_at)"
            " VALUES (:case_master_id, :crime_no, :score, :band, :components_json, :computed_at)",
            rows,
        )

    def for_case(self, case_master_id: int) -> dict[str, Any] | None:
        rows = self._store.query(
            "SELECT * FROM cip_case_priority WHERE case_master_id = :c", {"c": case_master_id}
        )
        if not rows:
            return None
        row = rows[0]
        row["components"] = json.loads(row.pop("components_json"))
        return row

    def top(self, case_ids: Sequence[int], limit: int = 20) -> list[dict[str, Any]]:
        if not case_ids:
            return []
        fragment, params = in_clause("c", list(case_ids))
        params["limit"] = limit
        rows = self._store.query(
            f"SELECT * FROM cip_case_priority WHERE case_master_id IN ({fragment})"
            " ORDER BY score DESC LIMIT :limit",
            params,
        )
        for row in rows:
            row["components"] = json.loads(row.pop("components_json"))
        return rows


class FinancialRepository:
    """SYNTHETIC EXTENSION — the source ER schema has no financial table."""

    IS_EXTENSION = True

    def __init__(self, store: DataStore) -> None:
        self._store = store

    def replace_all(self, transactions: Sequence[dict[str, Any]]) -> int:
        self._store.execute("DELETE FROM ext_financial_transaction")
        if not transactions:
            return 0
        rows = [{**txn, "created_at": _now()} for txn in transactions]
        return self._store.execute_many(
            "INSERT OR REPLACE INTO ext_financial_transaction (txn_id, case_master_id, from_kind, from_ref,"
            " from_label, to_kind, to_ref, to_label, amount, currency, txn_date, channel, is_extension, created_at)"
            " VALUES (:txn_id, :case_master_id, :from_kind, :from_ref, :from_label, :to_kind, :to_ref,"
            " :to_label, :amount, :currency, :txn_date, :channel, 1, :created_at)",
            rows,
        )

    def for_refs(self, refs: Sequence[str]) -> list[dict[str, Any]]:
        if not refs:
            return []
        fragment, params = in_clause("r", list(refs))
        return self._store.query(
            f"SELECT * FROM ext_financial_transaction WHERE from_ref IN ({fragment})"
            f" OR to_ref IN ({fragment}) ORDER BY txn_date",
            params,
        )

    def for_cases(self, case_ids: Sequence[int]) -> list[dict[str, Any]]:
        if not case_ids:
            return []
        fragment, params = in_clause("c", list(case_ids))
        return self._store.query(
            f"SELECT * FROM ext_financial_transaction WHERE case_master_id IN ({fragment}) ORDER BY txn_date",
            params,
        )

    def neighbourhood(self, refs: Sequence[str], *, limit: int = 2000) -> list[dict[str, Any]]:
        """The subject's transfers plus their counterparties' other transfers.

        A hop chain, a fan-in hub or a broker position is a property of a
        neighbourhood; none of them are visible in one account's rows. This is
        one hop only, and capped, because the next hop out reaches most of the
        graph and stops describing the subject at all.
        """
        direct = self.for_refs(refs)
        if not direct:
            return []
        counterparties = {str(t["from_ref"]) for t in direct} | {str(t["to_ref"]) for t in direct}
        counterparties.update(str(r) for r in refs)
        fragment, params = in_clause("r", sorted(counterparties))
        params["lim"] = limit
        return self._store.query(
            f"SELECT * FROM ext_financial_transaction WHERE from_ref IN ({fragment})"
            f" OR to_ref IN ({fragment}) ORDER BY txn_date LIMIT :lim",
            params,
        )

    def all_transactions(self) -> list[dict[str, Any]]:
        return self._store.query("SELECT * FROM ext_financial_transaction ORDER BY txn_date")

    def count(self) -> int:
        rows = self._store.query("SELECT COUNT(*) AS n FROM ext_financial_transaction")
        return int(rows[0]["n"]) if rows else 0
