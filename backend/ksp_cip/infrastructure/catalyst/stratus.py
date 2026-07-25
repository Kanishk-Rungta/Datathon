"""Catalyst Stratus file store adapter.

Same protocol as :class:`LocalFileStore`. Key validation is identical, so a
key that works locally works in Stratus and vice versa — which matters because
export URLs and landing-zone paths are written into the database.
"""

from __future__ import annotations

import json
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from ...config import Settings
from ...domain.errors import NotFoundError, ProviderError, ValidationError
from ..observability import get_logger
from .datastore import CatalystAuth

LOGGER = get_logger(__name__)

INVALID_KEY_CHARS = set('\\:*?"<>|')


def validate_key(key: str) -> str:
    cleaned = (key or "").strip().lstrip("/")
    if not cleaned:
        raise ValidationError("File key must not be empty")
    if ".." in cleaned.split("/"):
        raise ValidationError("File key must not traverse directories", key=key)
    if any(char in INVALID_KEY_CHARS for char in cleaned):
        raise ValidationError("File key contains an unsupported character", key=key)
    return cleaned


class StratusFileStore:
    backend = "stratus"

    def __init__(self, settings: Settings, auth: CatalystAuth | None = None) -> None:
        self._settings = settings
        self._auth = auth or CatalystAuth(settings)
        self._bucket = settings.catalyst_stratus_bucket
        self._base = (
            f"{settings.catalyst_base_url.rstrip('/')}"
            f"/baas/v1/project/{settings.catalyst_project_id}/bucket/{self._bucket}/object"
        )

    def write_bytes(self, key: str, payload: bytes, content_type: str = "application/octet-stream") -> str:
        cleaned = validate_key(key)
        self._call("PUT", cleaned, payload, content_type)
        LOGGER.info("stratus_object_written", extra={"key": cleaned, "bytes": len(payload)})
        return self.url_for(cleaned)

    def write_text(self, key: str, payload: str, content_type: str = "text/plain") -> str:
        return self.write_bytes(key, payload.encode("utf-8"), content_type)

    def read_bytes(self, key: str) -> bytes:
        cleaned = validate_key(key)
        return self._call("GET", cleaned, None, None)

    def exists(self, key: str) -> bool:
        try:
            self.read_bytes(key)
            return True
        except NotFoundError:
            return False

    def list_keys(self, prefix: str) -> list[str]:
        query = urllib_parse.urlencode({"prefix": validate_key(prefix)})
        raw = self._call("GET", f"?{query}", None, None, raw_path=True)
        payload: dict[str, Any] = json.loads(raw.decode("utf-8"))
        return [str(item.get("key")) for item in payload.get("data", []) if item.get("key")]

    def url_for(self, key: str) -> str:
        return f"/api/v1/files/{validate_key(key)}"

    def _call(self, method: str, key: str, payload: bytes | None,
              content_type: str | None, *, raw_path: bool = False) -> bytes:
        url = f"{self._base}{key if raw_path else '/' + urllib_parse.quote(key)}"
        request = urllib_request.Request(url, data=payload, method=method)
        request.add_header("Authorization", f"Zoho-oauthtoken {self._auth.token()}")
        request.add_header("ENVIRONMENT", self._settings.catalyst_environment)
        if content_type:
            request.add_header("Content-Type", content_type)
        try:
            with urllib_request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib_error.HTTPError as exc:  # pragma: no cover - network path
            if exc.code == 404:
                raise NotFoundError("Object not found in Stratus", key=key) from exc
            raise ProviderError("Stratus request failed", provider="stratus", status=exc.code) from exc
        except urllib_error.URLError as exc:  # pragma: no cover - network path
            raise ProviderError("Stratus is unreachable", provider="stratus") from exc
