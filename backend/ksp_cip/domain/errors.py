"""Domain-level error taxonomy.

These are transport-agnostic. ``ksp_cip.interface.api.errors`` maps them to
RFC 9457 problem-details responses (architecture §11.2).
"""

from __future__ import annotations

from typing import Any


class CIPError(Exception):
    """Base class for every error the platform raises deliberately."""

    code = "cip_error"
    http_status = 500
    title = "Platform error"

    def __init__(self, detail: str, **context: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.context: dict[str, Any] = context

    def to_problem(self, instance: str | None = None) -> dict[str, Any]:
        problem: dict[str, Any] = {
            "type": f"https://cip.ksp.gov.in/problems/{self.code}",
            "title": self.title,
            "status": self.http_status,
            "detail": self.detail,
            "code": self.code,
        }
        if instance:
            problem["instance"] = instance
        if self.context:
            problem["context"] = self.context
        return problem


class ValidationError(CIPError):
    code = "validation_error"
    http_status = 422
    title = "Request failed validation"


class NotFoundError(CIPError):
    code = "not_found"
    http_status = 404
    title = "Resource not found"


class AuthenticationError(CIPError):
    code = "authentication_failed"
    http_status = 401
    title = "Authentication failed"


class AuthorizationError(CIPError):
    code = "not_authorized"
    http_status = 403
    title = "Not authorized for this scope"


class ConflictError(CIPError):
    code = "conflict"
    http_status = 409
    title = "Conflicting state"


class DataQualityError(CIPError):
    code = "data_quality_blocker"
    http_status = 422
    title = "Batch failed a BLOCKER data-quality check"


class ProviderError(CIPError):
    code = "provider_unavailable"
    http_status = 502
    title = "Upstream provider unavailable"


class EvidenceMissingError(CIPError):
    """Raised when an answer would carry a factual claim with no citation.

    This is the structural enforcement of the "zero uncited claims" contract
    (architecture §9.2, plan §6.10). It is a *bug detector*: reaching it means
    an agent tried to emit an unsupported claim.
    """

    code = "evidence_missing"
    http_status = 500
    title = "Answer suppressed: claim without traceable evidence"


class RateLimitedError(CIPError):
    code = "rate_limited"
    http_status = 429
    title = "Rate limit exceeded"
