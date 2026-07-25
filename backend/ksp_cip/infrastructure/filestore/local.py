"""Local filesystem implementation of the :class:`FileStore` port.

Mirrors Catalyst Stratus semantics: opaque string keys with ``/`` separators,
write-once objects for ingest batches, and a URL the API can hand back to the
client. Keys are validated so a key can never escape the store root.
"""

from __future__ import annotations

import re
from pathlib import Path

from ...domain.errors import ValidationError

_KEY_RE = re.compile(r"^[A-Za-z0-9._\-/]{1,300}$")


class LocalFileStore:
    def __init__(self, root: Path, *, url_prefix: str = "/api/v1/files") -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._url_prefix = url_prefix.rstrip("/")

    @property
    def root(self) -> Path:
        return self._root

    def _resolve(self, key: str) -> Path:
        if not _KEY_RE.match(key or "") or ".." in key:
            raise ValidationError("Invalid object key", key=key)
        path = (self._root / key).resolve()
        if not str(path).startswith(str(self._root.resolve())):
            raise ValidationError("Object key escapes store root", key=key)
        return path

    def write_bytes(self, key: str, payload: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return key

    def write_text(self, key: str, payload: str, content_type: str = "text/plain") -> str:
        return self.write_bytes(key, payload.encode("utf-8"), content_type)

    def read_bytes(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.exists():
            from ...domain.errors import NotFoundError

            raise NotFoundError("Object not found", key=key)
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        try:
            return self._resolve(key).exists()
        except ValidationError:
            return False

    def list_keys(self, prefix: str) -> list[str]:
        base = self._root
        results: list[str] = []
        for path in sorted(base.rglob("*")):
            if path.is_file():
                rel = str(path.relative_to(base))
                if rel.startswith(prefix):
                    results.append(rel)
        return results

    def url_for(self, key: str) -> str:
        return f"{self._url_prefix}/{key}"
