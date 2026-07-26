"""Replaceable-data cache.

The rule from ``implementationv2.md`` §6.3, enforced here rather than left to
reviewer discipline: **the cache is never the source of truth.** It holds
master lookups, reference labels and other data that can be recomputed from the
curated tables at any moment. It must not hold an authorization decision, an
audit event, or a piece of evidence — losing the cache must cost latency and
nothing else.

Two implementations share one interface:

* :class:`InProcessCache` — the local default. Per-process, TTL-bound.
* :class:`CatalystCache` — Catalyst Cache segment over the same interface.

Both are deliberately *fail-open on read*: a cache error returns a miss and the
caller recomputes, because a degraded cache must never take the platform down.
Writes fail open too, for the same reason.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from ...config import Settings
from ..observability import get_logger

LOGGER = get_logger(__name__)

DEFAULT_TTL_SECONDS = 3600


class InProcessCache:
    """Thread-safe TTL cache scoped to one process."""

    backend = "memory"

    def __init__(self, *, default_ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._entries: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._default_ttl = default_ttl_seconds

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= time.time():
                self._entries.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        expires_at = time.time() + (ttl_seconds or self._default_ttl)
        with self._lock:
            self._entries[key] = (expires_at, value)

    def invalidate(self, prefix: str = "") -> int:
        """Drop everything under ``prefix``; empty prefix clears the cache.

        Called after a successful intelligence refresh so the console never
        shows master data from before the load (§8.1).
        """
        with self._lock:
            if not prefix:
                removed = len(self._entries)
                self._entries.clear()
                return removed
            doomed = [key for key in self._entries if key.startswith(prefix)]
            for key in doomed:
                self._entries.pop(key, None)
            return len(doomed)

    def get_or_set(self, key: str, factory: Callable[[], Any], ttl_seconds: int | None = None) -> Any:
        hit = self.get(key)
        if hit is not None:
            return hit
        value = factory()
        self.set(key, value, ttl_seconds)
        return value


class CatalystCache:
    """Catalyst Cache segment behind the same interface as :class:`InProcessCache`.

    Not exercised against a live Catalyst project in this build; that is stated
    here rather than implied. Because every method fails open, an unavailable
    or misbehaving cache degrades to "always recompute" instead of an outage —
    which is why binding this before it has been live-tested is safe.
    """

    backend = "catalyst"

    def __init__(self, settings: Settings, auth: Any | None = None) -> None:
        from .datastore import CatalystAuth

        self._settings = settings
        self._auth = auth or CatalystAuth(settings)
        self._segment = settings.catalyst_cache_segment
        self._default_ttl = settings.catalyst_cache_ttl_seconds
        self._base = (
            f"{settings.catalyst_base_url.rstrip('/')}"
            f"/baas/v1/project/{settings.catalyst_project_id}/cache"
        )
        # A local mirror keeps hot keys out of the network path entirely and
        # provides the fail-open answer when Catalyst Cache is unreachable.
        self._mirror = InProcessCache(default_ttl_seconds=self._default_ttl)

    def get(self, key: str) -> Any | None:
        mirrored = self._mirror.get(key)
        if mirrored is not None:
            return mirrored
        try:
            payload = self._call("GET", f"?cacheKey={urllib_parse.quote(key)}", None)
        except Exception as exc:  # noqa: BLE001 - a cache miss must never raise
            LOGGER.warning("cache_read_failed", extra={"error": type(exc).__name__})
            return None
        value = (payload.get("data") or {}).get("cache_value")
        if value is None:
            return None
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            decoded = value
        self._mirror.set(key, decoded)
        return decoded

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        self._mirror.set(key, value, ttl_seconds)
        body = json.dumps({
            "cache_name": self._segment,
            "cache_key": key,
            "cache_value": json.dumps(value, ensure_ascii=False, default=str),
            "expiry_in_hours": max(1, (ttl_seconds or self._default_ttl) // 3600),
        }).encode("utf-8")
        try:
            self._call("POST", "", body)
        except Exception as exc:  # noqa: BLE001 - a cache write must never raise
            LOGGER.warning("cache_write_failed", extra={"error": type(exc).__name__})

    def invalidate(self, prefix: str = "") -> int:
        # Catalyst Cache has no prefix delete; the local mirror is cleared and
        # remote entries age out on their TTL. Callers therefore must not rely
        # on invalidation for correctness — only for freshness.
        return self._mirror.invalidate(prefix)

    def get_or_set(self, key: str, factory: Callable[[], Any], ttl_seconds: int | None = None) -> Any:
        hit = self.get(key)
        if hit is not None:
            return hit
        value = factory()
        self.set(key, value, ttl_seconds)
        return value

    def _call(self, method: str, suffix: str, body: bytes | None) -> dict[str, Any]:
        request = urllib_request.Request(f"{self._base}{suffix}", data=body, method=method)
        request.add_header("Authorization", f"Zoho-oauthtoken {self._auth.token()}")
        request.add_header("Content-Type", "application/json")
        request.add_header("ENVIRONMENT", self._settings.catalyst_environment)
        with urllib_request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
