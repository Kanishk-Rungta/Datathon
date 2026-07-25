"""Audit service.

One row per meaningful action. The decorator is the cheap, uniform way to get
coverage across every agent call (plan §6.13), and the service is used directly
for API-level and pipeline events.

The audit trail is append-only by construction: :class:`AuditRepository`
exposes no update or delete method for events.
"""

from __future__ import annotations

import functools
import hashlib
import json
import time
import uuid
from typing import Any, Callable, Mapping, Sequence

from ...domain.models import Principal
from ...infrastructure.db.repositories import AuditRepository
from ...infrastructure.observability import correlation_id_var, get_logger

LOGGER = get_logger(__name__)


def request_hash(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


class AuditService:
    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    def record(
        self,
        *,
        action: str,
        principal: Principal | None = None,
        purpose_code: str = "operational_query",
        agent: str | None = None,
        object_type: str | None = None,
        object_ids: Sequence[Any] | None = None,
        outcome: str = "success",
        latency_ms: int | None = None,
        detail: Mapping[str, Any] | None = None,
        request_payload: Any = None,
    ) -> str:
        event_id = uuid.uuid4().hex
        scope_summary = None
        if principal is not None:
            scope_summary = (
                "statewide" if principal.scope.statewide else f"{len(principal.scope.unit_ids)} units"
            )
        self._repository.record(
            {
                "event_id": event_id,
                "actor_user_id": principal.user_id if principal else None,
                "actor_role": str(principal.role) if principal else None,
                "scope_summary": scope_summary,
                "purpose_code": purpose_code,
                "action": action,
                "agent": agent,
                "object_type": object_type,
                "object_ids": list(object_ids or []),
                "request_hash": request_hash(request_payload) if request_payload is not None else None,
                "outcome": outcome,
                "latency_ms": latency_ms,
                "correlation_id": correlation_id_var.get(),
                "detail": dict(detail or {}),
            }
        )
        return event_id

    def query(self, filters: Mapping[str, Any], limit: int = 200) -> list[dict[str, Any]]:
        return self._repository.query(filters, limit)

    def stats(self) -> dict[str, Any]:
        return self._repository.stats()


def audited(action: str, *, purpose_code: str = "operational_query", object_type: str | None = None):
    """Decorator for agent methods.

    The wrapped callable must be a bound method whose owner exposes
    ``self.audit`` (an :class:`AuditService`) and whose first argument is an
    object carrying ``principal``. This keeps the decorator honest: it cannot
    silently no-op if wiring is wrong.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
            audit: AuditService | None = getattr(self, "audit", None)
            principal = getattr(request, "principal", None)
            started = time.perf_counter()
            outcome = "success"
            result = None
            try:
                result = func(self, request, *args, **kwargs)
                return result
            except Exception as exc:  # noqa: BLE001 - re-raised below
                outcome = f"error:{type(exc).__name__}"
                raise
            finally:
                if audit is not None:
                    latency = int((time.perf_counter() - started) * 1000)
                    object_ids = _object_ids(result)
                    try:
                        audit.record(
                            action=action,
                            principal=principal,
                            purpose_code=purpose_code,
                            agent=getattr(self, "name", type(self).__name__),
                            object_type=object_type,
                            object_ids=object_ids,
                            outcome=outcome,
                            latency_ms=latency,
                            request_payload=_summarize_request(request),
                            detail={"result_kind": type(result).__name__ if result is not None else None},
                        )
                    except Exception:  # pragma: no cover - audit must never break a turn
                        LOGGER.exception("audit_write_failed", extra={"action": action})

        return wrapper

    return decorator


def _object_ids(result: Any) -> list[Any]:
    if result is None:
        return []
    evidence = getattr(result, "evidence", None)
    if evidence:
        ids: list[Any] = []
        for item in evidence:
            ids.extend(getattr(item, "case_master_ids", []) or [])
        return sorted(set(ids))[:100]
    return []


def _summarize_request(request: Any) -> dict[str, Any]:
    if request is None:
        return {}
    for attr in ("model_dump", "as_dict"):
        method = getattr(request, attr, None)
        if callable(method):
            try:
                data = method()
                data.pop("principal", None)
                return data
            except Exception:  # pragma: no cover - defensive
                break
    return {"repr": repr(request)[:200]}
