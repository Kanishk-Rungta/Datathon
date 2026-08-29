"""AI4Bharat speech adapter — self-hosted ASR / NMT / TTS.

Why this exists alongside :mod:`.service`'s Bhashini client: Bhashini is a
hosted government service reached with a credentialed account. AI4Bharat
publishes the *model weights* (IndicConformer for ASR, Indic-Parler-TTS for
TTS, IndicTrans2 for translation), so the same capability can be run on
hardware the deployment already controls. That difference matters here for
two reasons beyond cost:

* **Residency.** FIR audio never leaves the machines running the platform.
  Bhashini keeps it inside a government service; self-hosting keeps it inside
  *this* deployment, which is a strictly stronger position for case data.
* **Availability.** There is no quota and no third-party outage to inherit.

This class is only the *client*. The models run in a separate process — see
``speech-service/`` and ``docs/voice-ai4bharat.md`` — because loading PyTorch
into the API worker would put a multi-gigabyte dependency into every Catalyst
deployment artifact for a feature most requests never touch.

Transport is :mod:`urllib` from the standard library, deliberately, not
``httpx``: ``httpx`` is a *dev-only* dependency in ``pyproject.toml`` and is
absent from every deployment artifact's ``requirements.txt``. The Bhashini and
hosted-LLM adapters import it lazily at runtime and would therefore raise
``ImportError`` if they were ever enabled in a deployed function; this adapter
does not repeat that mistake.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from ...config.settings import Settings
from ...domain.enums import Language
from ...domain.errors import ProviderError
from ..observability import get_logger
from .service import LocalLexiconLanguageService

LOGGER = get_logger(__name__)

#: Audio container formats the speech service accepts. Anything else is
#: rejected here rather than forwarded, so a malformed upload fails at the
#: boundary with a clear message instead of deep inside a model loader.
SUPPORTED_MIME_TYPES = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
    "audio/mpeg": "mp3",
    "audio/mp4": "mp4",
}


def normalise_mime_type(mime_type: str) -> str:
    """Map a request content type onto a container name the service knows.

    Raises :class:`ProviderError` for anything unsupported. Codecs parameters
    (``audio/webm;codecs=opus``) are stripped — the container is what matters.
    """
    base = (mime_type or "").split(";")[0].strip().lower()
    fmt = SUPPORTED_MIME_TYPES.get(base)
    if fmt is None:
        raise ProviderError(
            f"Unsupported audio format '{base or 'unknown'}'. Supported: "
            + ", ".join(sorted(SUPPORTED_MIME_TYPES)),
            provider=AI4BharatLanguageService.provider_name,
        )
    return fmt


class AI4BharatLanguageService:
    """Client for a self-hosted AI4Bharat speech service.

    Language detection stays with the local lexicon (a Unicode-block test needs
    no model), and translation falls back to it when the service is unreachable
    so a speech outage never silently degrades into an *untranslated* answer
    presented as a translated one.
    """

    provider_name = "ai4bharat"
    is_full_fidelity = True

    def __init__(self, settings: Settings, fallback: LocalLexiconLanguageService | None = None) -> None:
        if not settings.ai4bharat_base_url:
            raise ProviderError(
                "AI4Bharat selected but KSPCIP_AI4BHARAT_BASE_URL is not configured",
                provider=self.provider_name,
            )
        self._settings = settings
        self._base = settings.ai4bharat_base_url.rstrip("/")
        self._fallback = fallback or LocalLexiconLanguageService()

    # ----------------------------------------------------------- detection
    def detect(self, text: str) -> str:
        # A Unicode-block test is exact and free; asking a remote service to
        # tell Kannada from English would add a network round trip and a
        # failure mode for no accuracy gain.
        return self._fallback.detect(text)

    # --------------------------------------------------------------- http
    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base}{path}"
        body = json.dumps(payload).encode("utf-8")
        request = urllib_request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        try:
            with urllib_request.urlopen(request, timeout=self._settings.ai4bharat_timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            LOGGER.error("ai4bharat_http_error", extra={"status": exc.code, "path": path})
            raise ProviderError(
                "AI4Bharat speech service returned an error",
                provider=self.provider_name, status=exc.code, response_detail=detail,
            ) from exc
        except urllib_error.URLError as exc:
            raise ProviderError(
                "AI4Bharat speech service is unreachable", provider=self.provider_name,
            ) from exc
        except json.JSONDecodeError as exc:
            raise ProviderError(
                "AI4Bharat speech service returned a malformed response",
                provider=self.provider_name,
            ) from exc

    # --------------------------------------------------------- translation
    def translate(self, text: str, *, source: str, target: str) -> str:
        if not text or source == target:
            return text
        try:
            data = self._post("/translate", {"text": text, "source": source, "target": target})
        except ProviderError:
            # Degrading to the glossary is safe *because* the platform reports
            # which provider answered; it is never presented as full fidelity.
            LOGGER.warning("ai4bharat_translate_fallback", extra={"source": source, "target": target})
            return self._fallback.translate(text, source=source, target=target)
        translated = data.get("text")
        return translated if isinstance(translated, str) and translated else text

    # --------------------------------------------------------------- voice
    def transcribe(self, audio: bytes, *, language: str, mime_type: str) -> str:
        if not audio:
            raise ProviderError("No audio was supplied to transcribe", provider=self.provider_name)
        ceiling = self._settings.voice_max_audio_bytes
        if len(audio) > ceiling:
            raise ProviderError(
                f"Audio is {len(audio)} bytes, above the {ceiling}-byte limit for one utterance",
                provider=self.provider_name,
            )
        audio_format = normalise_mime_type(mime_type)
        data = self._post("/asr", {
            "audio_base64": base64.b64encode(audio).decode("ascii"),
            "audio_format": audio_format,
            "language": language,
            "model": self._settings.ai4bharat_asr_model,
        })
        text = data.get("text")
        if not isinstance(text, str):
            raise ProviderError(
                "AI4Bharat ASR response contained no transcript", provider=self.provider_name,
            )
        # An empty transcript is a real answer — silence, or speech the model
        # could not resolve. The caller reports it as such rather than guessing.
        return text.strip()

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        if not text:
            return None
        try:
            data = self._post("/tts", {
                "text": text,
                "language": language,
                "model": self._settings.ai4bharat_tts_model,
                "speaker": self._settings.ai4bharat_tts_speaker,
            })
        except ProviderError:
            # TTS is an enhancement to an answer that has already been composed
            # and evidenced. Losing the audio must not lose the answer.
            LOGGER.warning("ai4bharat_tts_unavailable", extra={"language": language})
            return None
        content = data.get("audio_base64")
        if not isinstance(content, str) or not content:
            return None
        try:
            return base64.b64decode(content)
        except (ValueError, TypeError):
            LOGGER.warning("ai4bharat_tts_bad_payload")
            return None


def supported_languages() -> tuple[str, ...]:
    """Languages this build routes to the speech service.

    Kannada and English are what the platform reasons in today; the underlying
    AI4Bharat models cover far more Indic languages, so widening this is a
    configuration and evaluation question, not a code change.
    """
    return (Language.KANNADA.value, Language.ENGLISH.value)
