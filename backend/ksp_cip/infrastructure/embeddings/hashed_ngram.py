"""Deterministic hashed n-gram TF-IDF embeddings.

Why not a transformer: the architecture's production choice is BGE-M3 on a
GPU footprint (§9.2), which is unavailable in the hackathon target and would
make the platform depend on a model download at runtime. This model is a real,
fitted vector space — not a stub:

  * features = word unigrams + word bigrams + character 4-grams
  * character n-grams give robustness to transliteration variance
    (Shivakumar / Sivakumar) and work on Kannada script unchanged
  * signed feature hashing into a fixed dimensionality (no vocabulary to ship)
  * IDF fitted over the case corpus and persisted, so scores are stable
  * L2-normalised, so cosine similarity is a dot product

Swapping in BGE-M3 later is an adapter change: ``EmbeddingModel`` is the port,
and every vector row carries ``model_name`` so re-embedding is a backfill.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Iterable, Sequence

import numpy as np

_TOKEN_RE = re.compile(r"[\w\u0C80-\u0CFF]+", flags=re.UNICODE)
_CHAR_NGRAM = 4


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").casefold())


def _features(text: str) -> list[str]:
    words = _tokens(text)
    features: list[str] = [f"w:{w}" for w in words]
    features.extend(f"b:{a}_{b}" for a, b in zip(words, words[1:]))
    joined = " ".join(words)
    if len(joined) >= _CHAR_NGRAM:
        features.extend(f"c:{joined[i:i + _CHAR_NGRAM]}" for i in range(len(joined) - _CHAR_NGRAM + 1))
    return features


def _bucket(feature: str, dimensions: int) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    index = value % dimensions
    sign = 1.0 if (value >> 63) & 1 else -1.0
    return index, sign


class HashedNgramEmbeddingModel:
    def __init__(self, *, model_name: str = "hashed-char-ngram-tfidf-v1", dimensions: int = 512) -> None:
        self._model_name = model_name
        self._dimensions = dimensions
        self._idf: dict[int, float] = {}
        self._doc_count = 0

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def is_fitted(self) -> bool:
        return bool(self._idf)

    @property
    def doc_count(self) -> int:
        return self._doc_count

    # ------------------------------------------------------------------ fit
    def fit(self, corpus: Iterable[str]) -> "HashedNgramEmbeddingModel":
        document_frequency: Counter[int] = Counter()
        count = 0
        for text in corpus:
            count += 1
            seen: set[int] = set()
            for feature in _features(text):
                index, _ = _bucket(feature, self._dimensions)
                seen.add(index)
            document_frequency.update(seen)
        self._doc_count = count
        self._idf = {
            index: math.log((count + 1.0) / (freq + 1.0)) + 1.0
            for index, freq in document_frequency.items()
        }
        return self

    def export_state(self) -> dict[str, object]:
        return {
            "model_name": self._model_name,
            "dimensions": self._dimensions,
            "doc_count": self._doc_count,
            "idf": {str(k): round(v, 6) for k, v in self._idf.items()},
        }

    def load_state(self, state: dict[str, object]) -> "HashedNgramEmbeddingModel":
        self._model_name = str(state.get("model_name", self._model_name))
        self._dimensions = int(state.get("dimensions", self._dimensions))  # type: ignore[arg-type]
        self._doc_count = int(state.get("doc_count", 0))  # type: ignore[arg-type]
        idf = state.get("idf") or {}
        self._idf = {int(k): float(v) for k, v in dict(idf).items()}  # type: ignore[arg-type]
        return self

    # ---------------------------------------------------------------- embed
    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        matrix = np.zeros((len(texts), self._dimensions), dtype=np.float32)
        default_idf = math.log(self._doc_count + 1.0) + 1.0 if self._doc_count else 1.0
        for row, text in enumerate(texts):
            counts: Counter[tuple[int, float]] = Counter()
            for feature in _features(text):
                counts[_bucket(feature, self._dimensions)] += 1
            if not counts:
                continue
            max_tf = max(counts.values())
            for (index, sign), tf in counts.items():
                weight = (0.5 + 0.5 * tf / max_tf) * self._idf.get(index, default_idf)
                matrix[row, index] += sign * weight
            norm = float(np.linalg.norm(matrix[row]))
            if norm > 0:
                matrix[row] /= norm
        return matrix.tolist()


def cosine_similarity(query: Sequence[float], matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity of one L2-normalised query against a normalised matrix."""
    vector = np.asarray(query, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    if norm > 0:
        vector = vector / norm
    if matrix.size == 0:
        return np.zeros((0,), dtype=np.float32)
    return matrix @ vector
