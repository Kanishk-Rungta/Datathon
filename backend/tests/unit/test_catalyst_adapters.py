"""Catalyst adapter behaviour that must hold without a live project.

These cover the parts where a mistake is silent rather than loud: a session
document that outlives its TTL, a cache that takes the platform down when it is
unavailable, an identity token accepted without a verified signature, and a
deployment that stores case exports somewhere they will vanish.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from ksp_cip.config import Settings
from ksp_cip.config.settings import (
    DataStoreBackend,
    Environment,
    FileStoreBackend,
    IdentityBackend,
    LanguageProviderName,
)
from ksp_cip.domain.errors import AuthenticationError, ValidationError
from ksp_cip.infrastructure.catalyst.cache import InProcessCache
from ksp_cip.infrastructure.catalyst.identity import CatalystIdentityProvider
from ksp_cip.infrastructure.catalyst.nosql import MAX_DOCUMENT_BYTES, CatalystKeyValueStore


class FakeStore:
    """Records statements so the adapter's SQL intent can be asserted."""

    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []
        self.executed: list[tuple[str, dict]] = []

    def query(self, sql, params=None):
        return self.rows

    def execute(self, sql, params=None):
        self.executed.append((sql, dict(params or {})))
        return 1


# ------------------------------------------------------------------- NoSQL


class TestKeyValueDiscipline:
    def test_an_unknown_namespace_is_refused(self):
        store = CatalystKeyValueStore(FakeStore())
        with pytest.raises(ValidationError):
            store.put("anything_goes", "k", {"a": 1})

    def test_every_write_carries_an_expiry(self):
        """A document without a TTL makes the retention policy a fiction."""
        fake = FakeStore()
        CatalystKeyValueStore(fake).put("agent_scratchpad", "k", {"a": 1})
        _sql, params = fake.executed[-1]
        assert params["e"], "no expires_at was written"

    def test_an_explicit_ttl_overrides_the_namespace_default(self):
        fake = FakeStore()
        CatalystKeyValueStore(fake).put("session_state", "k", {"a": 1}, ttl_seconds=60)
        _sql, params = fake.executed[-1]
        assert params["e"]

    def test_an_oversized_document_is_refused(self):
        """Case narrative belongs in audited tables, not a scratchpad blob."""
        fake = FakeStore()
        store = CatalystKeyValueStore(fake)
        with pytest.raises(ValidationError):
            store.put("agent_scratchpad", "k", {"brief_facts": "x" * (MAX_DOCUMENT_BYTES + 1)})
        assert not fake.executed, "an oversized document reached the store"

    def test_keys_are_qualified_by_user(self):
        """Session ids are not globally unique; pins must not cross users."""
        assert CatalystKeyValueStore.qualify("u1", "s") != CatalystKeyValueStore.qualify("u2", "s")

    def test_an_expired_document_is_not_returned(self):
        assert CatalystKeyValueStore(FakeStore(rows=[])).get("session_state", "k") is None


# ------------------------------------------------------------------- cache


class TestCacheIsNeverLoadBearing:
    def test_a_miss_returns_none_rather_than_raising(self):
        assert InProcessCache().get("absent") is None

    def test_entries_expire(self):
        cache = InProcessCache()
        cache.set("k", "v", ttl_seconds=-1)
        assert cache.get("k") is None

    def test_invalidate_by_prefix_leaves_other_keys(self):
        cache = InProcessCache()
        cache.set("master:district", 1)
        cache.set("master:unit", 2)
        cache.set("other:thing", 3)
        assert cache.invalidate("master:") == 2
        assert cache.get("other:thing") == 3

    def test_invalidate_without_a_prefix_clears_everything(self):
        cache = InProcessCache()
        cache.set("a", 1)
        cache.set("b", 2)
        assert cache.invalidate() == 2
        assert cache.get("a") is None

    def test_get_or_set_computes_once(self):
        cache = InProcessCache()
        calls = []

        def factory():
            calls.append(1)
            return "value"

        assert cache.get_or_set("k", factory) == "value"
        assert cache.get_or_set("k", factory) == "value"
        assert len(calls) == 1


# ---------------------------------------------------------------- identity


def _token(claims: dict, secret: str = "s3cret", alg: str = "HS256") -> str:
    def seg(payload: dict) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    head = seg({"alg": alg, "typ": "JWT"})
    body = seg(claims)
    signature = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


class FakeUsers:
    def __init__(self, account=None):
        self._account = account

    def by_external_subject(self, subject):
        return self._account if self._account else None

    def by_username(self, username):
        return None


class FakeAuthorization:
    def __init__(self, principal=None):
        self._principal = principal

    def build_principal(self, account):
        return self._principal


def _provider(users, authorization, **overrides):
    settings = Settings(
        jwt_secret="s3cret",
        catalyst_auth_issuer="https://issuer.example",
        catalyst_project_id="p",
        **overrides,
    )
    return CatalystIdentityProvider(users, authorization, settings)


class TestIdentityFailsClosed:
    def test_a_tampered_signature_is_rejected(self):
        provider = _provider(FakeUsers(), FakeAuthorization())
        token = _token({"sub": "u", "iss": "https://issuer.example", "exp": int(time.time()) + 60},
                       secret="wrong-secret")
        with pytest.raises(AuthenticationError):
            provider.verify(token)

    def test_an_expired_token_is_rejected(self):
        provider = _provider(FakeUsers(), FakeAuthorization())
        token = _token({"sub": "u", "iss": "https://issuer.example", "exp": int(time.time()) - 1})
        with pytest.raises(AuthenticationError):
            provider.verify(token)

    def test_a_foreign_issuer_is_rejected(self):
        provider = _provider(FakeUsers(), FakeAuthorization())
        token = _token({"sub": "u", "iss": "https://attacker.example", "exp": int(time.time()) + 60})
        with pytest.raises(AuthenticationError):
            provider.verify(token)

    def test_the_none_algorithm_is_rejected(self):
        """The classic JWT bypass must not be reachable."""
        provider = _provider(FakeUsers(), FakeAuthorization())
        token = _token({"sub": "u", "iss": "https://issuer.example", "exp": int(time.time()) + 60},
                       alg="none")
        with pytest.raises(AuthenticationError):
            provider.verify(token)

    def test_a_malformed_token_does_not_leak_a_parser_error(self):
        provider = _provider(FakeUsers(), FakeAuthorization())
        with pytest.raises(AuthenticationError):
            provider.verify("not-a-token")

    def test_an_authenticated_but_unmapped_subject_is_refused(self):
        """Catalyst says who you are; KSP-CIP still decides if you may enter."""
        provider = _provider(FakeUsers(account=None), FakeAuthorization())
        token = _token({"sub": "u", "iss": "https://issuer.example", "exp": int(time.time()) + 60})
        with pytest.raises(AuthenticationError, match="not authorised"):
            provider.principal_from_token(token)

    def test_an_account_with_no_scope_is_refused_not_widened(self):
        from ksp_cip.domain.enums import Role
        from ksp_cip.domain.models import Principal, UnitScope

        empty_scope = Principal(
            user_id="u", username="u", display_name="U", role=Role.INVESTIGATOR,
            permissions=frozenset(), scope=UnitScope(statewide=False),
        )
        provider = _provider(FakeUsers(account={"user_id": "u"}), FakeAuthorization(empty_scope))
        token = _token({"sub": "u", "iss": "https://issuer.example", "exp": int(time.time()) + 60})
        with pytest.raises(AuthenticationError, match="no usable unit scope"):
            provider.principal_from_token(token)


# ------------------------------------------------------------ deployability


class TestDeploymentValidation:
    def test_the_zero_credential_default_is_deployable(self):
        assert Settings().deployment_problems() == []

    def test_catalyst_datastore_with_local_filestore_is_refused(self):
        """Exports on a function filesystem do not survive a cold start."""
        settings = Settings(
            datastore_backend=DataStoreBackend.CATALYST,
            filestore_backend=FileStoreBackend.LOCAL,
            catalyst_project_id="p",
            catalyst_oauth_client_id="c", catalyst_oauth_client_secret="s",
            catalyst_oauth_refresh_token="r",
        )
        assert any("FILESTORE_BACKEND" in problem for problem in settings.deployment_problems())

    def test_catalyst_backend_without_a_project_id_is_refused(self):
        settings = Settings(filestore_backend=FileStoreBackend.CATALYST)
        assert any("PROJECT_ID" in problem for problem in settings.deployment_problems())

    def test_bhashini_without_credentials_is_refused(self):
        settings = Settings(language_provider=LanguageProviderName.BHASHINI)
        assert any("BHASHINI" in problem for problem in settings.deployment_problems())

    def test_the_placeholder_jwt_secret_is_refused_outside_local(self):
        settings = Settings(environment=Environment.PRODUCTION)
        assert any("JWT_SECRET" in problem for problem in settings.deployment_problems())

    def test_catalyst_identity_without_an_issuer_is_refused(self):
        settings = Settings(identity_backend=IdentityBackend.CATALYST, catalyst_project_id="p")
        assert any("AUTH_ISSUER" in problem for problem in settings.deployment_problems())

    def test_problems_never_echo_a_secret_value(self):
        settings = Settings(
            environment=Environment.PRODUCTION,
            language_provider=LanguageProviderName.BHASHINI,
            bhashini_user_id="super-secret-user",
            jwt_secret="dev-only-secret-change-me",
        )
        joined = " ".join(settings.deployment_problems())
        assert "super-secret-user" not in joined
