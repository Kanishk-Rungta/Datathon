"""Catalyst QuickML provider — the mandated backend for text LLMs (#11).

Contract taken from the project's own console sample: an OpenAI-shaped body
posted to /quickml/v1/project/<id>/glm/chat, authenticated with a refreshed
Catalyst OAuth token and the CATALYST-ORG header.

Live calls need the QuickML.deployment.READ scope; without it the endpoint
answers 401 INVALID_OAUTHSCOPE for either Authorization spelling. That is a
credential gap, so the wire contract is pinned here instead.
"""

from __future__ import annotations

import pytest

from ksp_cip.config import Settings
from ksp_cip.config.settings import LLMProviderName
from ksp_cip.domain.errors import ProviderError
from ksp_cip.infrastructure.llm.providers import CatalystQuickMLProvider


class _StubAuth:
    def token(self) -> str:
        return "tok-123"


def settings(**overrides) -> Settings:
    base = dict(
        llm_provider="quickml",
        catalyst_project_id="54586000000013047",
        catalyst_org_id="60080180501",
        catalyst_base_url="https://api.catalyst.zoho.in",
        llm_model="",
    )
    base.update(overrides)
    return Settings(**base)


def provider(**overrides) -> CatalystQuickMLProvider:
    return CatalystQuickMLProvider(settings=settings(**overrides), auth=_StubAuth())


class TestEndpoint:
    def test_the_url_is_built_from_the_project_id(self):
        assert provider()._url() == (
            "https://api.catalyst.zoho.in/quickml/v1/project/54586000000013047/glm/chat"
        )

    def test_an_explicit_base_url_overrides_the_catalyst_one(self):
        assert provider(llm_base_url="https://example.test")._url().startswith(
            "https://example.test/quickml/v1/"
        )

    def test_the_model_defaults_to_the_deployed_endpoint_model(self):
        assert provider()._model == "crm-di-glm47b_30b_it"

    def test_an_explicit_model_wins(self):
        assert provider(llm_model="other-model")._model == "other-model"


class TestRequestShape:
    def _capture(self, monkeypatch, response_json):
        seen = {}

        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return response_json

        def fake_post(url, headers=None, json=None, timeout=None):
            seen["url"], seen["headers"], seen["json"] = url, headers, json
            return _Response()

        import httpx

        monkeypatch.setattr(httpx, "post", fake_post)
        return seen

    def test_system_prompt_leads_the_messages(self, monkeypatch):
        seen = self._capture(monkeypatch, {"choices": [{"message": {"content": "hi"}}]})
        provider().invoke(system="SYS", messages=[{"role": "user", "content": "U"}],
                          max_tokens=64, temperature=0.2)
        assert seen["json"]["messages"][0] == {"role": "system", "content": "SYS"}
        assert seen["json"]["messages"][1] == {"role": "user", "content": "U"}

    def test_auth_and_org_headers_are_sent(self, monkeypatch):
        seen = self._capture(monkeypatch, {"choices": [{"message": {"content": "hi"}}]})
        provider().invoke(system="s", messages=[], max_tokens=8, temperature=0.0)
        assert seen["headers"]["Authorization"] == "Zoho-oauthtoken tok-123"
        assert seen["headers"]["CATALYST-ORG"] == "60080180501"

    def test_streaming_is_off(self, monkeypatch):
        """The gateway consumes a whole string; a stream would break it."""
        seen = self._capture(monkeypatch, {"choices": [{"message": {"content": "hi"}}]})
        provider().invoke(system="s", messages=[], max_tokens=8, temperature=0.0)
        assert seen["json"]["stream"] is False

    def test_the_org_header_is_omitted_when_unset(self, monkeypatch):
        seen = self._capture(monkeypatch, {"choices": [{"message": {"content": "hi"}}]})
        CatalystQuickMLProvider(settings=settings(catalyst_org_id=None), auth=_StubAuth()).invoke(
            system="s", messages=[], max_tokens=8, temperature=0.0
        )
        assert "CATALYST-ORG" not in seen["headers"]


class TestResponseHandling:
    def test_the_message_content_is_returned(self, monkeypatch):
        class _R:
            def raise_for_status(self): return None
            def json(self): return {"choices": [{"message": {"content": "  hello  "}}]}

        import httpx
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _R())
        assert provider().invoke(system="s", messages=[], max_tokens=8, temperature=0.0) == "hello"

    def test_no_choices_yields_empty_rather_than_raising(self, monkeypatch):
        class _R:
            def raise_for_status(self): return None
            def json(self): return {"choices": []}

        import httpx
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _R())
        assert provider().invoke(system="s", messages=[], max_tokens=8, temperature=0.0) == ""

    def test_a_transport_failure_becomes_a_provider_error(self, monkeypatch):
        """The gateway swallows ProviderError; an arbitrary exception would 500."""
        import httpx

        def boom(*a, **k):
            raise RuntimeError("connection reset")

        monkeypatch.setattr(httpx, "post", boom)
        with pytest.raises(ProviderError):
            provider().invoke(system="s", messages=[], max_tokens=8, temperature=0.0)


class TestWiring:
    def test_quickml_is_a_selectable_provider(self):
        assert LLMProviderName.QUICKML.value == "quickml"

    def test_the_gateway_builds_it_without_an_api_key(self):
        from ksp_cip.infrastructure.llm.gateway import LLMGatewayImpl

        built = LLMGatewayImpl._build_provider(settings(
            catalyst_oauth_client_id="a", catalyst_oauth_client_secret="b",
            catalyst_oauth_refresh_token="c",
        ))
        assert isinstance(built, CatalystQuickMLProvider)

    def test_it_is_not_treated_as_local(self):
        """PII redaction must apply: this provider leaves the platform."""
        from ksp_cip.infrastructure.llm.gateway import LLMGatewayImpl

        gateway = LLMGatewayImpl(settings(
            catalyst_oauth_client_id="a", catalyst_oauth_client_secret="b",
            catalyst_oauth_refresh_token="c",
        ), provider=provider())
        assert gateway.is_local is False
