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


def _bucket_origin(settings: Settings) -> str:
    """The host Stratus objects actually live on.

    Stratus is not served from the Data Store's ``/baas/v1/project/...`` API.
    Each bucket has its own origin, and the environment is part of the
    hostname rather than a header:

        https://<bucket>-development.zohostratus.<dc>/<object>

    Addressing it the way the rest of the Catalyst API is addressed returns
    404 for every request, including writes -- which is what broke PDF export.
    Confirmed live: ``cip-ingest-development.zohostratus.in`` accepts a PUT,
    while ``cip-ingest.zohostratus.in`` answers ``bucket_not_found``. The
    shape matches the Catalyst CLI's own uploader (``endpoints/lib/stratus.js``).
    """
    host = urllib_parse.urlparse(settings.catalyst_base_url).hostname or "api.catalyst.zoho.com"
    data_centre = host.rsplit(".", 1)[-1]          # api.catalyst.zoho.in -> "in"
    environment = (settings.catalyst_environment or "").strip().lower()
    # Only non-production environments carry a suffix, as the CLI does it.
    suffix = f"-{environment}" if environment and environment != "production" else ""
    return f"https://{settings.catalyst_stratus_bucket}{suffix}.zohostratus.{data_centre}"


class StratusFileStore:
    backend = "stratus"

    def __init__(self, settings: Settings, auth: CatalystAuth | None = None) -> None:
        self._settings = settings
        self._auth = auth or CatalystAuth(settings)
        self._bucket = settings.catalyst_stratus_bucket
        self._base = _bucket_origin(settings)

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
        """Refused: Stratus exposes no documented list-objects REST endpoint.

        This used to GET the bucket origin with a ``?prefix=`` query, which the
        live bucket answers with a bare ``405 Method Not Allowed`` — accurate
        but uninformative, and indistinguishable from a transient fault. The
        baas-hosted shapes (``/stratus/bucket/<b>/object`` and neighbours) all
        answer ``INVALID_URL_PATTERN``, and Zoho documents object listing only
        through the SDKs, not REST.

        Failing loudly here follows the same rule as ``PRAGMA`` on the Data
        Store: refuse an operation this backend cannot honestly perform rather
        than approximate it. Nothing in the application calls this — reads are
        by exact key — so the port keeps its shape without pretending.
        """
        raise ProviderError(
            "Stratus has no documented list-objects REST endpoint; "
            "objects are addressed by exact key on this backend.",
            provider="stratus", prefix=validate_key(prefix),
        )

    def url_for(self, key: str) -> str:
        return f"/api/v1/files/{validate_key(key)}"

    def _call(self, method: str, key: str, payload: bytes | None,
              content_type: str | None, *, raw_path: bool = False) -> bytes:
        url = f"{self._base}{key if raw_path else '/' + urllib_parse.quote(key)}"
        request = urllib_request.Request(url, data=payload, method=method)
        request.add_header("Authorization", f"Zoho-oauthtoken {self._auth.token()}")
        # The environment is part of the hostname here, not a header, and the
        # bucket origin is outside the Catalyst API -- so the usual
        # ENVIRONMENT/Accept headers do not apply. `compress: false` is what
        # the Catalyst CLI's own uploader sends.
        request.add_header("compress", "false")
        if content_type:
            request.add_header("Content-Type", content_type)
        try:
            with urllib_request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib_error.HTTPError as exc:  # pragma: no cover - network path
            body = exc.read().decode("utf-8", errors="replace")[:200]
            # Only a read can legitimately 404 on a missing object. A 404 on a
            # write means the bucket or endpoint is wrong, and reporting that
            # as "object not found" sent an earlier investigation the wrong
            # way entirely.
            if exc.code == 404 and method == "GET":
                raise NotFoundError("Object not found in Stratus", key=key) from exc
            if exc.code == 404:
                raise ProviderError(
                    f"Stratus rejected {method} {url}: {body or 'not found'}. "
                    "Check the bucket name and environment.",
                    provider="stratus", status=404, key=key) from exc
            raise ProviderError(f"Stratus request failed: {body}", provider="stratus",
                                status=exc.code, key=key) from exc
        except urllib_error.URLError as exc:  # pragma: no cover - network path
            raise ProviderError("Stratus is unreachable", provider="stratus") from exc
