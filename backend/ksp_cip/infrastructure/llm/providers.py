"""LLM provider adapters.

Design rule (ADR-0003): the LLM is an *orchestration and language* component.
It never produces a fact. Consequently every provider — including the local
deterministic one — is interchangeable without changing a single number in an
answer. The local provider exists so the platform is fully functional with no
credentials and so tests are hermetic.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Mapping, Sequence

from ...domain.errors import ProviderError


class BaseProvider(ABC):
    name = "base"

    @abstractmethod
    def invoke(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:  # pragma: no cover - abstract
        ...

    def estimate_tokens(self, text: str) -> int:
        # 4 characters per token is the standard rough estimate; good enough
        # for budget accounting, and it never influences answer content.
        return max(1, len(text) // 4)


class LocalDeterministicProvider(BaseProvider):
    """Offline provider: assembles output from the structured brief it is given.

    The orchestrator always passes the deterministic facts in the final user
    message under a ``FACTS:`` block. This provider returns that block's
    ``narrative`` field when present, otherwise a readable rendering of the
    facts. No randomness, no network, no invention.
    """

    name = "local"

    def invoke(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        last = ""
        for message in reversed(list(messages)):
            if message.get("role") == "user":
                last = message.get("content", "")
                break
        facts = _extract_facts_block(last)
        if facts is None:
            return ""
        narrative = facts.get("narrative")
        if isinstance(narrative, str) and narrative.strip():
            return narrative.strip()
        return _render_facts(facts)


def _extract_facts_block(text: str) -> dict[str, Any] | None:
    marker = "FACTS:"
    if marker not in text:
        return None
    blob = text.split(marker, 1)[1].strip()
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return None


def _render_facts(facts: Mapping[str, Any]) -> str:
    lines: list[str] = []
    for key, value in facts.items():
        if key.startswith("_"):
            continue
        if isinstance(value, (list, tuple)):
            rendered = "; ".join(str(item) for item in value[:10])
        elif isinstance(value, Mapping):
            rendered = ", ".join(f"{k}={v}" for k, v in list(value.items())[:10])
        else:
            rendered = str(value)
        lines.append(f"{key.replace('_', ' ').capitalize()}: {rendered}")
    return "\n".join(lines)


class _HTTPProvider(BaseProvider):
    def __init__(self, *, api_key: str | None, model: str, base_url: str | None, timeout: float) -> None:
        if not api_key:
            raise ProviderError(f"Provider '{self.name}' selected but no API key configured", provider=self.name)
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._timeout = timeout

    def _post(self, url: str, *, headers: Mapping[str, str], payload: Mapping[str, Any]) -> dict[str, Any]:
        import httpx

        try:
            response = httpx.post(url, headers=dict(headers), json=dict(payload), timeout=self._timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - adapter boundary
            raise ProviderError(f"{self.name} call failed: {exc}", provider=self.name) from exc


class AnthropicProvider(_HTTPProvider):
    name = "anthropic"

    def invoke(self, *, system: str, messages: Sequence[Mapping[str, str]], max_tokens: int, temperature: float) -> str:
        base = (self._base_url or "https://api.anthropic.com").rstrip("/")
        data = self._post(
            f"{base}/v1/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            payload={
                "model": self._model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
            },
        )
        parts = [block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"]
        return "\n".join(part for part in parts if part).strip()


class GeminiProvider(_HTTPProvider):
    name = "gemini"

    def invoke(self, *, system: str, messages: Sequence[Mapping[str, str]], max_tokens: int, temperature: float) -> str:
        base = (self._base_url or "https://generativelanguage.googleapis.com").rstrip("/")
        contents = [
            {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
            for m in messages
        ]
        data = self._post(
            f"{base}/v1beta/models/{self._model}:generateContent?key={self._api_key}",
            headers={"content-type": "application/json"},
            payload={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": contents,
                "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
            },
        )
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "\n".join(part.get("text", "") for part in parts).strip()


class OpenAICompatibleProvider(_HTTPProvider):
    """Covers Groq, together.ai, vLLM, and any OpenAI-shaped endpoint."""

    name = "openai_compatible"
    default_base_url = "https://api.openai.com/v1"

    def invoke(self, *, system: str, messages: Sequence[Mapping[str, str]], max_tokens: int, temperature: float) -> str:
        base = (self._base_url or self.default_base_url).rstrip("/")
        payload_messages = [{"role": "system", "content": system}]
        payload_messages.extend({"role": m["role"], "content": m["content"]} for m in messages)
        data = self._post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}", "content-type": "application/json"},
            payload={
                "model": self._model,
                "messages": payload_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        choices = data.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message", {}).get("content") or "").strip()


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"
    default_base_url = "https://api.groq.com/openai/v1"


PROVIDER_REGISTRY: dict[str, type[BaseProvider]] = {
    LocalDeterministicProvider.name: LocalDeterministicProvider,
    AnthropicProvider.name: AnthropicProvider,
    GeminiProvider.name: GeminiProvider,
    GroqProvider.name: GroqProvider,
    OpenAICompatibleProvider.name: OpenAICompatibleProvider,
}


# ---------------------------------------------------------------- redaction

_PHONE_RE = re.compile(r"\b(?:\+91[- ]?)?[6-9]\d{9}\b")
_AADHAAR_RE = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")


def redact_pii(text: str) -> str:
    """Pre-filter applied to any payload leaving the platform boundary.

    Case identifiers (CrimeNo) are intentionally preserved: they are the
    citation currency and are meaningless without the source system.
    """
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    text = _AADHAAR_RE.sub("[REDACTED_ID]", text)
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    return text
