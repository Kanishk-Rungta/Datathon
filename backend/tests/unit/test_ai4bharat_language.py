"""Contract tests for the self-hosted AI4Bharat speech adapter.

The rule these enforce is the one that matters for a speech provider: losing
speech must never lose, alter, or silently downgrade an *answer*. Translation
falls back to the glossary, synthesis returns nothing, and only transcription
— where there is no honest substitute for a transcript — raises.

The HTTP transport is faked at ``urlopen`` so every path here runs offline and
deterministically, matching how the Catalyst adapter is contract-tested.
"""

from __future__ import annotations

import base64
import json
import io
from urllib import error as urllib_error

import pytest

from ksp_cip.config.settings import Settings
from ksp_cip.domain.errors import ProviderError
from ksp_cip.infrastructure.language import build_language_service
from ksp_cip.infrastructure.language.ai4bharat import (
    AI4BharatLanguageService,
    normalise_mime_type,
)

BASE_URL = "http://speech.invalid:9100"


def settings(**overrides) -> Settings:
    return Settings(language_provider="ai4bharat", ai4bharat_base_url=BASE_URL, **overrides)


class FakeResponse(io.BytesIO):
    """Minimal stand-in for the object ``urlopen`` yields as a context manager."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_transport(monkeypatch, handler):
    """Route every urlopen call in the adapter through ``handler(url, body)``."""
    calls: list[tuple[str, dict]] = []

    def urlopen(request, timeout=None):  # noqa: ARG001 - signature must match
        body = json.loads(request.data.decode("utf-8"))
        calls.append((request.full_url, body))
        return handler(request.full_url, body)

    monkeypatch.setattr(
        "ksp_cip.infrastructure.language.ai4bharat.urllib_request.urlopen", urlopen
    )
    return calls


def json_response(payload: dict) -> FakeResponse:
    return FakeResponse(json.dumps(payload).encode("utf-8"))


class TestConfiguration:
    def test_the_provider_is_selected_when_configured(self):
        service = build_language_service(settings())
        assert service.provider_name == "ai4bharat"
        assert service.is_full_fidelity is True

    def test_a_missing_url_is_a_startup_problem_not_a_crash(self):
        problems = Settings(language_provider="ai4bharat").deployment_problems()
        assert any("AI4BHARAT_BASE_URL" in problem for problem in problems)

    def test_a_missing_url_degrades_to_the_local_glossary(self):
        # Reported by deployment_problems() above; the container refuses to
        # build. If one is built anyway, it must not pretend to be full fidelity.
        service = build_language_service(Settings(language_provider="ai4bharat"))
        assert service.provider_name == "local-lexicon"
        assert service.is_full_fidelity is False

    def test_construction_without_a_url_is_refused(self):
        with pytest.raises(ProviderError):
            AI4BharatLanguageService(Settings(language_provider="ai4bharat"))


class TestMimeValidation:
    @pytest.mark.parametrize("mime,expected", [
        ("audio/wav", "wav"),
        ("audio/webm;codecs=opus", "webm"),
        ("AUDIO/WEBM", "webm"),
        ("audio/ogg", "ogg"),
    ])
    def test_supported_containers_are_normalised(self, mime, expected):
        assert normalise_mime_type(mime) == expected

    @pytest.mark.parametrize("mime", ["application/pdf", "text/plain", "", "video/mp4"])
    def test_unsupported_containers_are_refused(self, mime):
        with pytest.raises(ProviderError):
            normalise_mime_type(mime)


class TestTranscription:
    def test_a_transcript_is_returned_and_stripped(self, monkeypatch):
        service = AI4BharatLanguageService(settings())
        calls = fake_transport(monkeypatch, lambda url, body: json_response({"text": "  ಕಳ್ಳತನ  "}))
        assert service.transcribe(b"audio", language="kn", mime_type="audio/wav") == "ಕಳ್ಳತನ"
        url, body = calls[0]
        assert url == f"{BASE_URL}/asr"
        assert body["language"] == "kn"
        assert base64.b64decode(body["audio_base64"]) == b"audio"

    def test_silence_transcribes_to_empty_rather_than_a_guess(self, monkeypatch):
        service = AI4BharatLanguageService(settings())
        fake_transport(monkeypatch, lambda url, body: json_response({"text": ""}))
        assert service.transcribe(b"audio", language="kn", mime_type="audio/wav") == ""

    def test_oversized_audio_is_refused_before_any_network_call(self, monkeypatch):
        service = AI4BharatLanguageService(settings(voice_max_audio_bytes=1024))
        calls = fake_transport(monkeypatch, lambda url, body: json_response({"text": "x"}))
        with pytest.raises(ProviderError, match="above the"):
            service.transcribe(b"x" * 1025, language="kn", mime_type="audio/wav")
        assert calls == [], "an oversized upload must not reach the speech service"

    def test_empty_audio_is_refused(self):
        with pytest.raises(ProviderError):
            AI4BharatLanguageService(settings()).transcribe(b"", language="kn", mime_type="audio/wav")

    def test_an_unreachable_service_raises_rather_than_inventing_a_transcript(self, monkeypatch):
        service = AI4BharatLanguageService(settings())

        def down(request, timeout=None):  # noqa: ARG001
            raise urllib_error.URLError("connection refused")

        monkeypatch.setattr(
            "ksp_cip.infrastructure.language.ai4bharat.urllib_request.urlopen", down
        )
        with pytest.raises(ProviderError, match="unreachable"):
            service.transcribe(b"audio", language="kn", mime_type="audio/wav")

    def test_a_response_without_a_transcript_is_an_error(self, monkeypatch):
        service = AI4BharatLanguageService(settings())
        fake_transport(monkeypatch, lambda url, body: json_response({"unexpected": True}))
        with pytest.raises(ProviderError, match="no transcript"):
            service.transcribe(b"audio", language="kn", mime_type="audio/wav")

    def test_malformed_json_is_a_typed_error(self, monkeypatch):
        service = AI4BharatLanguageService(settings())
        monkeypatch.setattr(
            "ksp_cip.infrastructure.language.ai4bharat.urllib_request.urlopen",
            lambda request, timeout=None: FakeResponse(b"not json"),
        )
        with pytest.raises(ProviderError, match="malformed"):
            service.transcribe(b"audio", language="kn", mime_type="audio/wav")


class TestSynthesis:
    def test_audio_is_decoded_from_base64(self, monkeypatch):
        service = AI4BharatLanguageService(settings())
        payload = base64.b64encode(b"RIFFwav").decode("ascii")
        fake_transport(monkeypatch, lambda url, body: json_response({"audio_base64": payload}))
        assert service.synthesize("ಉತ್ತರ", language="kn") == b"RIFFwav"

    def test_an_outage_loses_the_audio_but_not_the_answer(self, monkeypatch):
        service = AI4BharatLanguageService(settings())

        def down(request, timeout=None):  # noqa: ARG001
            raise urllib_error.URLError("connection refused")

        monkeypatch.setattr(
            "ksp_cip.infrastructure.language.ai4bharat.urllib_request.urlopen", down
        )
        # None, not an exception: the composed and evidenced answer still ships.
        assert service.synthesize("ಉತ್ತರ", language="kn") is None

    def test_an_undecodable_payload_yields_no_audio(self, monkeypatch):
        service = AI4BharatLanguageService(settings())
        fake_transport(monkeypatch, lambda url, body: json_response({"audio_base64": "!!!not base64!!!"}))
        assert service.synthesize("ಉತ್ತರ", language="kn") is None

    def test_empty_text_is_not_sent_to_the_service(self, monkeypatch):
        service = AI4BharatLanguageService(settings())
        calls = fake_transport(monkeypatch, lambda url, body: json_response({"audio_base64": ""}))
        assert service.synthesize("", language="kn") is None
        assert calls == []


class TestTranslation:
    def test_a_translation_is_returned(self, monkeypatch):
        service = AI4BharatLanguageService(settings())
        fake_transport(monkeypatch, lambda url, body: json_response({"text": "ಕಳ್ಳತನ"}))
        assert service.translate("theft", source="en", target="kn") == "ಕಳ್ಳತನ"

    def test_an_outage_falls_back_to_the_offline_glossary(self, monkeypatch):
        service = AI4BharatLanguageService(settings())

        def down(request, timeout=None):  # noqa: ARG001
            raise urllib_error.URLError("connection refused")

        monkeypatch.setattr(
            "ksp_cip.infrastructure.language.ai4bharat.urllib_request.urlopen", down
        )
        # The glossary knows this term, so the fallback is visible in the output.
        assert "ಕಳ್ಳತನ" in service.translate("theft", source="en", target="kn")

    def test_identical_languages_short_circuit(self, monkeypatch):
        service = AI4BharatLanguageService(settings())
        calls = fake_transport(monkeypatch, lambda url, body: json_response({"text": "unused"}))
        assert service.translate("theft", source="en", target="en") == "theft"
        assert calls == []


class TestDetection:
    def test_script_detection_needs_no_network_call(self, monkeypatch):
        service = AI4BharatLanguageService(settings())
        calls = fake_transport(monkeypatch, lambda url, body: json_response({}))
        assert service.detect("ಮೈಸೂರು ಜಿಲ್ಲೆ") == "kn"
        assert service.detect("Mysuru district") == "en"
        assert calls == []
