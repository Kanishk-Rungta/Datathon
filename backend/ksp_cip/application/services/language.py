"""Turn-level language orchestration (plan §5.2).

The platform reasons entirely in English and translates only at the two edges.
This service owns that boundary so no agent ever sees a non-English string.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...domain.enums import Language
from ...domain.ports import LanguageService


@dataclass(slots=True)
class InboundText:
    original: str
    english: str
    language: Language
    translated: bool


class ConversationLanguageService:
    def __init__(self, provider: LanguageService) -> None:
        self._provider = provider

    @property
    def provider_name(self) -> str:
        return self._provider.provider_name

    @property
    def is_full_fidelity(self) -> bool:
        return bool(getattr(self._provider, "is_full_fidelity", False))

    def detect(self, text: str) -> Language:
        return Language(self._provider.detect(text))

    def to_english(self, text: str, *, declared: Language | None = None) -> InboundText:
        language = declared or self.detect(text)
        if language is Language.ENGLISH:
            return InboundText(original=text, english=text, language=language, translated=False)
        english = self._provider.translate(text, source=language.value, target=Language.ENGLISH.value)
        return InboundText(original=text, english=english or text, language=language, translated=bool(english))

    def from_english(self, text: str, *, target: Language) -> str:
        if target is Language.ENGLISH or not text:
            return text
        return self._provider.translate(text, source=Language.ENGLISH.value, target=target.value)

    def transcribe(self, audio: bytes, *, language: Language, mime_type: str) -> str:
        return self._provider.transcribe(audio, language=language.value, mime_type=mime_type)

    def synthesize(self, text: str, *, language: Language) -> bytes | None:
        return self._provider.synthesize(text, language=language.value)
