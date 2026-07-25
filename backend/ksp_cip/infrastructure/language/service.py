"""Language services: detection, translation, speech-to-text, text-to-speech.

Two implementations of the same port:

``LocalLexiconLanguageService``
    Deterministic, offline, credential-free. Translation is glossary- and
    template-driven over the policing domain vocabulary. It is honest about
    what it is: :attr:`is_full_fidelity` is ``False`` and the API surfaces a
    ``language_provider`` field so the console can show a notice.

``BhashiniLanguageService``
    The production path (plan §5.2). Bhashini is Government-of-India language
    infrastructure (MeitY / National Language Translation Mission), which keeps
    voice and text inside an Indian, government-operated service — the right
    residency posture for FIR data.
"""

from __future__ import annotations

import base64
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...config.settings import Settings
from ...domain.enums import Language
from ...domain.errors import ProviderError
from ..observability import get_logger

LOGGER = get_logger(__name__)
LEXICON_PATH = Path(__file__).resolve().parents[2] / "resources" / "lexicon" / "kn_en.json"

KANNADA_BLOCK = re.compile(r"[\u0C80-\u0CFF]")
_WORD_SPLIT = re.compile(r"(\W+)", flags=re.UNICODE)


@lru_cache(maxsize=1)
def load_lexicon() -> dict[str, Any]:
    return json.loads(LEXICON_PATH.read_text(encoding="utf-8"))


class LocalLexiconLanguageService:
    provider_name = "local-lexicon"
    is_full_fidelity = False

    def __init__(self) -> None:
        lex = load_lexicon()
        self._kn_to_en: dict[str, str] = {}
        for group in ("districts", "crime_terms", "domain_terms", "time_terms", "question_terms"):
            self._kn_to_en.update(lex[group])
        self._numerals: dict[str, str] = lex["numerals"]
        self._en_to_kn: dict[str, str] = dict(lex["answer_phrases"])
        for kn, en in lex["districts"].items():
            self._en_to_kn.setdefault(en.lower(), kn)
        # Longest-first so multi-word terms win over their constituents.
        self._kn_keys = sorted(self._kn_to_en, key=len, reverse=True)
        self._en_keys = sorted(self._en_to_kn, key=len, reverse=True)

    # ----------------------------------------------------------- detection
    def detect(self, text: str) -> str:
        if not text:
            return Language.ENGLISH.value
        kannada_chars = len(KANNADA_BLOCK.findall(text))
        return Language.KANNADA.value if kannada_chars >= max(1, len(text.strip()) // 8) else Language.ENGLISH.value

    # --------------------------------------------------------- translation
    def translate(self, text: str, *, source: str, target: str) -> str:
        if not text or source == target:
            return text
        if source == Language.KANNADA.value and target == Language.ENGLISH.value:
            return self._kn_to_english(text)
        if source == Language.ENGLISH.value and target == Language.KANNADA.value:
            return self._english_to_kn(text)
        return text

    def _kn_to_english(self, text: str) -> str:
        working = "".join(self._numerals.get(ch, ch) for ch in text)
        for key in self._kn_keys:
            if key in working:
                working = working.replace(key, f" {self._kn_to_en[key]} ")
        working = KANNADA_BLOCK.sub(" ", working)
        return re.sub(r"\s+", " ", working).strip()

    def _english_to_kn(self, text: str) -> str:
        working = text
        for key in self._en_keys:
            pattern = re.compile(rf"\b{re.escape(key)}\b", flags=re.IGNORECASE)
            working = pattern.sub(self._en_to_kn[key], working)
        return working

    # ---------------------------------------------------------------- voice
    def transcribe(self, audio: bytes, *, language: str, mime_type: str) -> str:
        raise ProviderError(
            "Server-side speech recognition needs the Bhashini provider. "
            "The console uses the browser's on-device speech recognition when it is unavailable.",
            provider=self.provider_name,
        )

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        return None


class BhashiniLanguageService:
    """Bhashini (ULCA / Dhruva) pipeline client.

    Pipeline compute is a single POST carrying an ordered task list, so ASR →
    NMT → TTS is one round trip. Config (model ids per task) is fetched once
    from the ULCA config endpoint and cached for the process lifetime.
    """

    provider_name = "bhashini"
    is_full_fidelity = True

    def __init__(self, settings: Settings, fallback: LocalLexiconLanguageService | None = None) -> None:
        if not settings.bhashini_user_id or not settings.bhashini_api_key:
            raise ProviderError("Bhashini selected but BHASHINI_USER_ID/API_KEY are not configured",
                                provider=self.provider_name)
        self._settings = settings
        self._fallback = fallback or LocalLexiconLanguageService()
        self._pipeline_config: dict[str, Any] | None = None

    def detect(self, text: str) -> str:
        return self._fallback.detect(text)

    # --------------------------------------------------------------- config
    def _config(self, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        import httpx

        try:
            response = httpx.post(
                self._settings.bhashini_config_url,
                headers={
                    "userID": self._settings.bhashini_user_id or "",
                    "ulcaApiKey": self._settings.bhashini_api_key or "",
                    "Content-Type": "application/json",
                },
                json={"pipelineTasks": tasks, "pipelineRequestConfig": {
                    "pipelineId": self._settings.bhashini_pipeline_id}},
                timeout=self._settings.bhashini_timeout_seconds,
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - adapter boundary
            raise ProviderError(f"Bhashini config call failed: {exc}", provider=self.provider_name) from exc

    def _compute(self, tasks: list[dict[str, Any]], inputs: dict[str, Any]) -> dict[str, Any]:
        import httpx

        config = self._config([{"taskType": t["taskType"], "config": t["configQuery"]} for t in tasks])
        endpoint = config.get("pipelineInferenceAPIEndPoint", {})
        callback_url = endpoint.get("callbackUrl")
        auth = endpoint.get("inferenceApiKey", {})
        if not callback_url:
            raise ProviderError("Bhashini config returned no inference endpoint", provider=self.provider_name)

        pipeline_tasks = []
        config_response = {item["taskType"]: item for item in config.get("pipelineResponseConfig", [])}
        for task in tasks:
            entry = config_response.get(task["taskType"], {})
            service_id = ""
            configs = entry.get("config") or []
            if configs:
                service_id = configs[0].get("serviceId", "")
            pipeline_tasks.append({
                "taskType": task["taskType"],
                "config": {**task["configQuery"], "serviceId": service_id},
            })

        try:
            response = httpx.post(
                callback_url,
                headers={auth.get("name", "Authorization"): auth.get("value", ""), "Content-Type": "application/json"},
                json={"pipelineTasks": pipeline_tasks, "inputData": inputs},
                timeout=self._settings.bhashini_timeout_seconds,
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - adapter boundary
            raise ProviderError(f"Bhashini compute call failed: {exc}", provider=self.provider_name) from exc

    # ---------------------------------------------------------------- tasks
    def translate(self, text: str, *, source: str, target: str) -> str:
        if not text or source == target:
            return text
        try:
            data = self._compute(
                [{"taskType": "translation", "configQuery": {"language": {"sourceLanguage": source,
                                                                          "targetLanguage": target}}}],
                {"input": [{"source": text}]},
            )
            outputs = data.get("pipelineResponse", [])
            if outputs and outputs[0].get("output"):
                return outputs[0]["output"][0].get("target", text)
            return text
        except ProviderError:
            LOGGER.warning("bhashini_translate_fallback", extra={"source": source, "target": target})
            return self._fallback.translate(text, source=source, target=target)

    def transcribe(self, audio: bytes, *, language: str, mime_type: str) -> str:
        payload = base64.b64encode(audio).decode("ascii")
        data = self._compute(
            [{"taskType": "asr", "configQuery": {"language": {"sourceLanguage": language},
                                                 "audioFormat": _audio_format(mime_type),
                                                 "samplingRate": 16000}}],
            {"audio": [{"audioContent": payload}]},
        )
        outputs = data.get("pipelineResponse", [])
        if outputs and outputs[0].get("output"):
            return outputs[0]["output"][0].get("source", "")
        return ""

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        try:
            data = self._compute(
                [{"taskType": "tts", "configQuery": {"language": {"sourceLanguage": language}, "gender": "female"}}],
                {"input": [{"source": text}]},
            )
        except ProviderError:
            LOGGER.warning("bhashini_tts_unavailable")
            return None
        outputs = data.get("pipelineResponse", [])
        if outputs and outputs[0].get("audio"):
            content = outputs[0]["audio"][0].get("audioContent")
            if content:
                return base64.b64decode(content)
        return None


def _audio_format(mime_type: str) -> str:
    mapping = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3",
               "audio/flac": "flac", "audio/webm": "webm", "audio/ogg": "ogg"}
    return mapping.get(mime_type.split(";")[0].strip().lower(), "wav")


def build_language_service(settings: Settings) -> LocalLexiconLanguageService | BhashiniLanguageService:
    from ...config.settings import LanguageProviderName

    local = LocalLexiconLanguageService()
    if settings.language_provider is LanguageProviderName.BHASHINI:
        try:
            return BhashiniLanguageService(settings, fallback=local)
        except ProviderError as exc:
            LOGGER.warning("bhashini_unavailable_using_local", extra={"error": str(exc)})
    return local
