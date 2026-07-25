"""Hybrid retrieval with access control inside the query (architecture §9.2).

Two properties matter more than raw relevance here:

1. **ACL pre-filtering.** The candidate set is narrowed to the caller's unit
   subtree *before* ranking, not after. Leakage-by-snippet is therefore
   structurally impossible rather than merely unlikely.
2. **Citation binding.** Every result carries its ``CaseMasterID`` and
   ``CrimeNo``. Retrieval returns records, never prose; prose is composed by
   the deterministic answer composer from those records.

Corpus construction follows the architecture: chunked ``BriefFacts`` plus a
deterministically rendered "case card" of structured facts, which gives the
retriever clean signal for queries phrased in administrative language
("chargesheeted theft cases in Mysuru").
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np

from ...domain.models import UnitScope
from ...infrastructure.db.repositories import CaseRepository, EmbeddingRepository
from ...infrastructure.embeddings import HashedNgramEmbeddingModel, cosine_similarity
from ...infrastructure.observability import get_logger

LOGGER = get_logger(__name__)

CHUNK_TOKENS = 90
CHUNK_OVERLAP = 14


@dataclass(slots=True)
class RetrievedDocument:
    doc_id: str
    case_master_id: int
    crime_no: str
    unit_id: int | None
    text_snippet: str
    similarity: float
    keyword_boost: float
    score: float
    source_table: str


def chunk_text(text: str, *, size: int = CHUNK_TOKENS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    if len(words) <= size:
        return [" ".join(words)]
    chunks: list[str] = []
    step = max(1, size - overlap)
    for start in range(0, len(words), step):
        piece = words[start:start + size]
        if not piece:
            break
        chunks.append(" ".join(piece))
        if start + size >= len(words):
            break
    return chunks


def case_card(row: dict[str, Any]) -> str:
    """Deterministic structured rendering of a case for the retriever."""
    parts = [
        f"FIR {row.get('CrimeNo')}",
        f"crime type {row.get('crime_sub_head') or 'unclassified'}",
        f"district {row.get('DistrictName') or 'unknown'}",
    ]
    return ". ".join(part for part in parts if part) + "."


class RetrievalService:
    def __init__(
        self,
        cases: CaseRepository,
        embeddings: EmbeddingRepository,
        model: HashedNgramEmbeddingModel,
        *,
        top_k: int = 8,
        min_similarity: float = 0.18,
    ) -> None:
        self._cases = cases
        self._embeddings = embeddings
        self._model = model
        self._top_k = top_k
        self._min_similarity = min_similarity
        self._lock = threading.RLock()
        self._matrix: np.ndarray | None = None
        self._meta: list[dict[str, Any]] = []

    @property
    def model_name(self) -> str:
        return self._model.model_name

    # ------------------------------------------------------------- indexing
    def rebuild(self) -> dict[str, Any]:
        """Fit the model on the corpus and persist every document vector."""
        rows = self._cases.brief_facts_corpus()
        documents: list[dict[str, Any]] = []
        for row in rows:
            case_id = int(row["CaseMasterID"])
            crime_no = str(row["CrimeNo"])
            unit_id = row.get("PoliceStationID")
            card = case_card(row)
            documents.append({
                "doc_id": f"card:{case_id}",
                "source_table": "curated_CaseMaster",
                "source_pk": case_id,
                "case_master_id": case_id,
                "unit_id": unit_id,
                "lang": "en",
                "text_snippet": card,
            })
            for index, chunk in enumerate(chunk_text(str(row.get("BriefFacts") or ""))):
                documents.append({
                    "doc_id": f"facts:{case_id}:{index}",
                    "source_table": "curated_CaseMaster",
                    "source_pk": case_id,
                    "case_master_id": case_id,
                    "unit_id": unit_id,
                    "lang": "en",
                    "text_snippet": chunk,
                })
            kannada = str(row.get("cip_brief_facts_kn") or "")
            if kannada:
                documents.append({
                    "doc_id": f"facts_kn:{case_id}",
                    "source_table": "curated_CaseMaster",
                    "source_pk": case_id,
                    "case_master_id": case_id,
                    "unit_id": unit_id,
                    "lang": "kn",
                    "text_snippet": kannada[:1200],
                })

        corpus = [doc["text_snippet"] for doc in documents]
        self._model.fit(corpus)
        vectors = self._model.embed(corpus)
        for doc, vector in zip(documents, vectors):
            doc["embedding"] = vector
        written = self._embeddings.replace_all(documents, self._model.model_name)
        self._embeddings.save_model_state(self._model.export_state())
        self.invalidate()
        return {
            "documents": written,
            "cases": len(rows),
            "model": self._model.model_name,
            "dimensions": self._model.dimensions,
        }

    def invalidate(self) -> None:
        with self._lock:
            self._matrix = None
            self._meta = []

    def warm(self) -> int:
        with self._lock:
            if self._matrix is not None:
                return len(self._meta)
            state = self._embeddings.load_model_state(self._model.model_name)
            if state:
                self._model.load_state(state)
            rows = self._embeddings.load_all(self._model.model_name)
            if not rows:
                self._matrix = np.zeros((0, self._model.dimensions), dtype=np.float32)
                self._meta = []
                return 0
            self._matrix = np.asarray([row["embedding"] for row in rows], dtype=np.float32)
            self._meta = [
                {
                    "doc_id": row["doc_id"],
                    "case_master_id": row.get("case_master_id"),
                    "unit_id": row.get("unit_id"),
                    "text_snippet": row["text_snippet"],
                    "source_table": row["source_table"],
                    "lang": row.get("lang", "en"),
                }
                for row in rows
            ]
            return len(self._meta)

    @property
    def document_count(self) -> int:
        self.warm()
        return len(self._meta)

    # ------------------------------------------------------------ searching
    def search(
        self,
        query: str,
        scope: UnitScope,
        *,
        top_k: int | None = None,
        boost_terms: Sequence[str] = (),
        exclude_case_ids: Iterable[int] = (),
    ) -> list[RetrievedDocument]:
        self.warm()
        with self._lock:
            matrix = self._matrix
            meta = self._meta
        if matrix is None or matrix.size == 0 or not query.strip():
            return []

        excluded = set(int(c) for c in exclude_case_ids)
        allowed_rows = [
            index
            for index, item in enumerate(meta)
            if (scope.statewide or (item["unit_id"] is not None and int(item["unit_id"]) in scope.unit_ids))
            and item["case_master_id"] not in excluded
        ]
        if not allowed_rows:
            return []

        candidate_matrix = matrix[allowed_rows]
        query_vector = self._model.embed_one(query)
        similarities = cosine_similarity(query_vector, candidate_matrix)

        boosts = [term.casefold() for term in boost_terms if term]
        crime_nos: dict[int, str] = {}
        results: list[RetrievedDocument] = []
        for position, row_index in enumerate(allowed_rows):
            similarity = float(similarities[position])
            item = meta[row_index]
            snippet_lower = item["text_snippet"].casefold()
            boost = 0.06 * sum(1 for term in boosts if term in snippet_lower)
            score = similarity + boost
            if score < self._min_similarity:
                continue
            results.append(
                RetrievedDocument(
                    doc_id=item["doc_id"],
                    case_master_id=int(item["case_master_id"]),
                    crime_no=crime_nos.get(int(item["case_master_id"]), ""),
                    unit_id=item["unit_id"],
                    text_snippet=item["text_snippet"],
                    similarity=round(similarity, 4),
                    keyword_boost=round(boost, 4),
                    score=round(score, 4),
                    source_table=item["source_table"],
                )
            )

        results.sort(key=lambda doc: doc.score, reverse=True)
        # Collapse to best chunk per case: the officer wants cases, not chunks.
        best_per_case: dict[int, RetrievedDocument] = {}
        for doc in results:
            if doc.case_master_id not in best_per_case:
                best_per_case[doc.case_master_id] = doc
        collapsed = list(best_per_case.values())[: (top_k or self._top_k)]

        summaries = {
            summary.case_master_id: summary
            for summary in self._cases.by_ids([doc.case_master_id for doc in collapsed], scope)
        }
        for doc in collapsed:
            summary = summaries.get(doc.case_master_id)
            if summary:
                doc.crime_no = summary.crime_no
        return [doc for doc in collapsed if doc.crime_no]

    def similar_to_case(
        self, case_master_id: int, scope: UnitScope, *, top_k: int | None = None
    ) -> list[RetrievedDocument]:
        summary = self._cases.by_id(case_master_id, scope)
        if summary is None:
            return []
        query = " ".join(
            part for part in [summary.brief_facts or "", summary.crime_sub_head or "", summary.district_name or ""]
            if part
        )
        boost_terms = [t for t in [summary.crime_sub_head or "", summary.district_name or ""] if t]
        return self.search(query, scope, top_k=top_k, boost_terms=boost_terms, exclude_case_ids=[case_master_id])
