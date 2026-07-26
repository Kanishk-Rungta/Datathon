"""Ports — the only way the application layer touches the outside world.

Every adapter in ``ksp_cip.infrastructure`` implements one of these protocols.
Swapping SQLite for Catalyst Data Store, or the local LLM for Gemini, is a
container binding change and nothing else (architecture §11.1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class DataStore(Protocol):
    """Relational store abstraction.

    Implementations MUST accept only parameterized statements. The SQLite and
    Catalyst adapters both use ``:name`` style parameters; adapters translate
    to their native dialect. Callers never interpolate user input.
    """

    def query(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]: ...

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> int: ...

    def execute_many(self, sql: str, rows: Sequence[Mapping[str, Any]]) -> int: ...

    def executescript(self, script: str) -> None: ...

    def transaction(self) -> Any:
        """Context manager giving atomic semantics."""

    def table_columns(self, table: str) -> list[str]:
        """Declared column names for ``table``, in schema order.

        This is the portable replacement for a repository constructing
        ``PRAGMA table_info(...)`` directly — a statement only SQLite
        understands. Callers that need "what columns does this table have"
        (the loader's schema-conformance check) go through this instead of
        building dialect-specific introspection SQL themselves.
        """


@runtime_checkable
class FileStore(Protocol):
    def write_bytes(self, key: str, payload: bytes, content_type: str = "application/octet-stream") -> str: ...

    def write_text(self, key: str, payload: str, content_type: str = "text/plain") -> str: ...

    def read_bytes(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def list_keys(self, prefix: str) -> list[str]: ...

    def url_for(self, key: str) -> str: ...


@runtime_checkable
class KeyValueStore(Protocol):
    """TTL-capable document store (Catalyst NoSQL / Cache stand-in)."""

    def put(self, namespace: str, key: str, value: Mapping[str, Any], ttl_seconds: int | None = None) -> None: ...

    def get(self, namespace: str, key: str) -> dict[str, Any] | None: ...

    def delete(self, namespace: str, key: str) -> None: ...

    def scan(self, namespace: str, key_prefix: str = "", limit: int = 500) -> list[dict[str, Any]]: ...

    def purge_expired(self) -> int: ...


@runtime_checkable
class LLMGateway(Protocol):
    """Provider-agnostic language model access (architecture §9.1).

    The gateway is used for *language*, never for facts: paraphrasing
    deterministic output, and optionally refining intent classification.
    """

    @property
    def provider_name(self) -> str: ...

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, str]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        purpose: str = "generic",
    ) -> str: ...

    def classify(
        self,
        *,
        system: str,
        user_text: str,
        labels: Sequence[str],
        purpose: str = "intent",
    ) -> tuple[str | None, float]: ...

    def usage(self) -> dict[str, Any]: ...


@runtime_checkable
class LanguageService(Protocol):
    """ASR / NMT / TTS (Bhashini in production, deterministic locally)."""

    @property
    def provider_name(self) -> str: ...

    def detect(self, text: str) -> str: ...

    def translate(self, text: str, *, source: str, target: str) -> str: ...

    def transcribe(self, audio: bytes, *, language: str, mime_type: str) -> str: ...

    def synthesize(self, text: str, *, language: str) -> bytes | None: ...


@runtime_checkable
class EmbeddingModel(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_one(self, text: str) -> list[float]: ...


@runtime_checkable
class AuditSink(Protocol):
    def record(self, event: Mapping[str, Any]) -> None: ...

    def query(self, filters: Mapping[str, Any], limit: int = 200) -> list[dict[str, Any]]: ...


__all__ = [
    "AuditSink",
    "Clock",
    "DataStore",
    "EmbeddingModel",
    "FileStore",
    "KeyValueStore",
    "LLMGateway",
    "LanguageService",
]
