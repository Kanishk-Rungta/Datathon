"""Authentication: password verification and session tokens.

Catalyst Authentication is the production identity provider (architecture
§12.1). For local execution the platform issues its own HS256 tokens against
the same ``Principal`` contract, so swapping to Catalyst Auth or an OIDC
federation replaces this class and nothing else.

Passwords use PBKDF2-HMAC-SHA256 with a per-user salt. No third-party crypto
dependency is required, and the iteration count is configuration.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
import uuid
from typing import Any, Mapping

from ...config.settings import Settings
from ...domain.errors import AuthenticationError
from ...domain.models import Principal
from ...infrastructure.db.repositories import UserRepository
from .authorization import AuthorizationService


def _b64url_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _b64url_decode(payload: str) -> bytes:
    padding = "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode(payload + padding)


class PasswordHasher:
    def __init__(self, iterations: int = 120_000) -> None:
        self._iterations = iterations

    def hash(self, password: str, salt: str | None = None) -> tuple[str, str]:
        salt = salt or secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), self._iterations)
        return salt, digest.hex()

    def verify(self, password: str, salt: str, expected_hash: str) -> bool:
        _, computed = self.hash(password, salt)
        return hmac.compare_digest(computed, expected_hash)


class TokenService:
    def __init__(self, secret: str, *, ttl_seconds: int = 43_200, algorithm: str = "HS256") -> None:
        if algorithm != "HS256":
            raise ValueError("Only HS256 is supported by the local token service")
        self._secret = secret.encode("utf-8")
        self._ttl = ttl_seconds

    def issue(self, principal: Principal) -> dict[str, Any]:
        now = int(time.time())
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "sub": principal.user_id,
            "username": principal.username,
            "role": str(principal.role),
            "iat": now,
            "exp": now + self._ttl,
            "jti": uuid.uuid4().hex,
        }
        segments = [
            _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ]
        signing_input = ".".join(segments).encode("ascii")
        signature = hmac.new(self._secret, signing_input, hashlib.sha256).digest()
        segments.append(_b64url_encode(signature))
        return {"access_token": ".".join(segments), "token_type": "Bearer", "expires_in": self._ttl}

    def verify(self, token: str) -> dict[str, Any]:
        """Verify a token, treating *any* structural failure as authentication failure.

        A malformed token must never surface as a decoding exception: that
        leaks the parser's internals through a 500 and hands an attacker a
        signal the caller was never meant to see.
        """
        try:
            header_b64, payload_b64, signature_b64 = token.split(".")
            signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
            expected = hmac.new(self._secret, signing_input, hashlib.sha256).digest()
            signature = _b64url_decode(signature_b64)
            payload = json.loads(_b64url_decode(payload_b64))
        except (ValueError, TypeError, binascii.Error, UnicodeDecodeError,
                json.JSONDecodeError) as exc:
            raise AuthenticationError("Malformed token") from exc
        if not hmac.compare_digest(expected, signature):
            raise AuthenticationError("Token signature is not valid")
        if not isinstance(payload, dict):
            raise AuthenticationError("Malformed token payload")
        if int(payload.get("exp", 0)) < int(time.time()):
            raise AuthenticationError("Token has expired")
        return payload


class IdentityService:
    def __init__(
        self,
        users: UserRepository,
        authorization: AuthorizationService,
        settings: Settings,
    ) -> None:
        self._users = users
        self._authorization = authorization
        self._hasher = PasswordHasher(settings.password_hash_iterations)
        self._tokens = TokenService(
            settings.jwt_secret,
            ttl_seconds=settings.access_token_ttl_seconds,
            algorithm=settings.jwt_algorithm,
        )

    @property
    def hasher(self) -> PasswordHasher:
        return self._hasher

    def register(self, *, username: str, password: str, display_name: str, role: str,
                 home_unit_id: int | None, district_id: int | None) -> str:
        salt, digest = self._hasher.hash(password)
        user_id = uuid.uuid5(uuid.NAMESPACE_URL, f"kspcip:user:{username}").hex
        self._users.create({
            "user_id": user_id,
            "username": username,
            "display_name": display_name,
            "role": role,
            "home_unit_id": home_unit_id,
            "district_id": district_id,
            "password_salt": salt,
            "password_hash": digest,
        })
        return user_id

    def authenticate(self, username: str, password: str) -> tuple[Principal, dict[str, Any]]:
        account = self._users.by_username(username)
        if not account or not self._hasher.verify(password, account["password_salt"], account["password_hash"]):
            raise AuthenticationError("Username or password is not correct")
        principal = self._authorization.build_principal(account)
        return principal, self._tokens.issue(principal)

    def principal_from_token(self, token: str) -> Principal:
        payload = self._tokens.verify(token)
        account = self._users.by_id(str(payload["sub"]))
        if not account:
            raise AuthenticationError("Account no longer exists or is inactive")
        return self._authorization.build_principal(account)
