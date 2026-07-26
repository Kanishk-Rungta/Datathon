"""Catalyst Authentication → :class:`Principal` mapping.

The division of responsibility (``implementationv2.md`` §6.1) is the whole
point of this module:

* **Catalyst Authentication decides who the person is.** It issues the token
  and owns MFA/SSO/session policy.
* **KSP-CIP decides what that person may see.** Role, home unit and district
  come from ``cip_user_account``, never from a claim in the token.

That split matters because a token claim is asserted by the identity provider,
while police scope is a decision the department owns. A deployment that trusted
a ``role`` claim would let anyone who can mint a token pick their own
authority.

Failure is closed at every step: an unverified signature, an unmapped subject,
a disabled account, or an account with no usable scope all raise
:class:`AuthenticationError`. There is no path here that assigns statewide
access by default.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Any, Mapping

from ...config import Settings
from ...domain.errors import AuthenticationError
from ...domain.models import Principal
from ...infrastructure.db.repositories import UserRepository
from ..observability import get_logger

LOGGER = get_logger(__name__)

#: Claims a token must carry before it is worth looking anything up.
REQUIRED_CLAIMS = ("sub", "iss", "exp")


def _b64url_decode(payload: str) -> bytes:
    padding = "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode(payload + padding)


class CatalystIdentityProvider:
    """Verifies a Catalyst/OIDC token and maps it to a local principal.

    Signature verification is HMAC-based here because that is what can be
    honestly implemented and tested without a live JWKS endpoint. A deployment
    using asymmetric (RS256) Catalyst tokens must supply the JWKS fetch in
    :meth:`_verify_signature` — the method is isolated for exactly that reason,
    and it refuses any algorithm it was not built to check rather than
    accepting an unverified token.
    """

    backend = "catalyst"

    def __init__(
        self,
        users: UserRepository,
        authorization: Any,
        settings: Settings,
        *,
        shared_secret: str | None = None,
    ) -> None:
        self._users = users
        self._authorization = authorization
        self._settings = settings
        self._issuer = settings.catalyst_auth_issuer
        self._audience = settings.catalyst_auth_audience
        self._secret = (shared_secret or settings.jwt_secret).encode("utf-8")

    # ---------------------------------------------------------------- public
    def principal_from_token(self, token: str) -> Principal:
        claims = self.verify(token)
        subject = str(claims["sub"])
        account = self._lookup_account(subject, claims)
        if not account:
            LOGGER.warning("identity_unmapped_subject", extra={"subject_hash": _hash(subject)})
            raise AuthenticationError(
                "This identity is authenticated but not authorised for KSP-CIP. "
                "An administrator must map it to a role and unit."
            )
        principal = self._authorization.build_principal(account)
        if not principal.scope.statewide and not principal.scope.unit_ids:
            # Refusing here is deliberate: an account whose scope resolves to
            # nothing must not silently fall through to "sees everything".
            raise AuthenticationError(
                "This account has no usable unit scope. Assign a home unit before granting access."
            )
        return principal

    def verify(self, token: str) -> dict[str, Any]:
        try:
            header_b64, payload_b64, signature_b64 = token.split(".")
            header = json.loads(_b64url_decode(header_b64))
            claims = json.loads(_b64url_decode(payload_b64))
            signature = _b64url_decode(signature_b64)
        except (ValueError, TypeError, binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AuthenticationError("Malformed identity token") from exc

        if not isinstance(claims, dict) or not isinstance(header, dict):
            raise AuthenticationError("Malformed identity token payload")

        self._verify_signature(header, f"{header_b64}.{payload_b64}".encode("ascii"), signature)

        missing = [claim for claim in REQUIRED_CLAIMS if claim not in claims]
        if missing:
            raise AuthenticationError("Identity token is missing required claims")

        if int(claims.get("exp", 0)) < int(time.time()):
            raise AuthenticationError("Identity token has expired")

        if self._issuer and str(claims.get("iss")) != self._issuer:
            raise AuthenticationError("Identity token was issued by an unexpected authority")

        if self._audience and self._audience not in _as_list(claims.get("aud")):
            raise AuthenticationError("Identity token is not addressed to this application")

        return claims

    # -------------------------------------------------------------- internals
    def _verify_signature(self, header: Mapping[str, Any], signing_input: bytes, signature: bytes) -> None:
        algorithm = str(header.get("alg", "")).upper()
        if algorithm != "HS256":
            # Never fall through to "accept unverified". An RS256 deployment
            # must add JWKS retrieval here first.
            raise AuthenticationError(
                f"Unsupported token signing algorithm '{algorithm or 'none'}'; "
                "this build verifies HS256 only"
            )
        expected = hmac.new(self._secret, signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, signature):
            raise AuthenticationError("Identity token signature is not valid")

    def _lookup_account(self, subject: str, claims: Mapping[str, Any]) -> dict[str, Any] | None:
        """Resolve the local account for an external subject.

        Preference order is stability-first: the immutable ``sub`` before the
        email, because an officer's email can be reassigned while their police
        scope should not follow it.
        """
        account = self._users.by_external_subject(subject)
        if account:
            return account
        email = claims.get("email") or claims.get("preferred_username")
        if email:
            return self._users.by_username(str(email))
        return None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _hash(value: str) -> str:
    """Short non-reversible digest, so logs can correlate without holding an id."""
    return hashlib.blake2s(value.encode("utf-8"), digest_size=8).hexdigest()
