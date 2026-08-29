"""LLM Gateway — the single door to every model call (architecture §9.1).

Responsibilities kept here, not in agents:
  * provider abstraction and hot-swap by configuration
  * versioned prompt registry (prompts live in ``resources/prompts``)
  * token accounting against a daily budget
  * PII redaction for any non-local provider
  * structured-output enforcement for classification
  * graceful degradation: a provider failure never fails a turn, because
    the deterministic layer has already produced the answer's facts.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Mapping, Sequence

from ...config.settings import LLMProviderName, Settings
from ...domain.errors import ProviderError
from ..observability import get_logger
from .providers import PROVIDER_REGISTRY, BaseProvider, LocalDeterministicProvider, redact_pii

LOGGER = get_logger(__name__)
PROMPT_ROOT = Path(__file__).resolve().parents[2] / "resources" / "prompts"


class PromptRegistry:
    """Loads versioned system prompts from disk.

    Prompts are reviewable independently of code (plan §10). File naming:
    ``<name>.v<version>.md``; the highest version wins unless pinned.
    """

    def __init__(self, root: Path = PROMPT_ROOT) -> None:
        self._root = root
        self._cache: dict[str, tuple[str, str]] = {}

    def get(self, name: str, version: str | None = None) -> tuple[str, str]:
        """Return ``(prompt_text, version_label)``."""
        cache_key = f"{name}:{version or 'latest'}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        candidates = sorted(self._root.glob(f"{name}.v*.md"))
        if not candidates:
            raise ProviderError(f"Prompt '{name}' not found in registry", prompt=name)
        chosen = None
        if version:
            for path in candidates:
                if path.name == f"{name}.v{version}.md":
                    chosen = path
                    break
        chosen = chosen or candidates[-1]
        label = chosen.name.split(".v", 1)[1].removesuffix(".md")
        result = (chosen.read_text(encoding="utf-8"), label)
        self._cache[cache_key] = result
        return result

    def list_prompts(self) -> list[dict[str, str]]:
        return [
            {"name": path.name.split(".v", 1)[0], "version": path.name.split(".v", 1)[1].removesuffix(".md")}
            for path in sorted(self._root.glob("*.v*.md"))
        ]


class LLMGatewayImpl:
    def __init__(self, settings: Settings, provider: BaseProvider | None = None) -> None:
        self._settings = settings
        self._provider = provider or self._build_provider(settings)
        self._prompts = PromptRegistry()
        self._lock = threading.Lock()
        self._tokens_in = 0
        self._tokens_out = 0
        self._calls = 0
        self._failures = 0

    @staticmethod
    def _build_provider(settings: Settings) -> BaseProvider:
        name = str(settings.llm_provider)
        provider_cls = PROVIDER_REGISTRY.get(name)
        if provider_cls is None:  # pragma: no cover - config guard
            raise ProviderError(f"Unknown LLM provider '{name}'", provider=name)
        if settings.llm_provider is LLMProviderName.LOCAL:
            return LocalDeterministicProvider()
        if settings.llm_provider is LLMProviderName.QUICKML:
            # QuickML authenticates with a refreshed Catalyst OAuth token, not
            # a static key, so it takes Settings rather than the api_key/model
            # tuple the other HTTP providers share.
            from .providers import CatalystQuickMLProvider

            return CatalystQuickMLProvider(settings=settings)
        return provider_cls(  # type: ignore[call-arg]
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
        )

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def prompts(self) -> PromptRegistry:
        return self._prompts

    @property
    def is_local(self) -> bool:
        return isinstance(self._provider, LocalDeterministicProvider)

    # ------------------------------------------------------------- calling
    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, str]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        purpose: str = "generic",
    ) -> str:
        prepared = list(messages)
        if not self.is_local:
            prepared = [{**m, "content": redact_pii(m.get("content", ""))} for m in prepared]
            system = redact_pii(system)
        budget_exceeded = self._budget_exceeded()
        if budget_exceeded:
            LOGGER.warning("llm_budget_exceeded", extra={"purpose": purpose})
            return ""
        tokens_in = self._provider.estimate_tokens(system + "".join(m.get("content", "") for m in prepared))
        try:
            output = self._provider.invoke(
                system=system,
                messages=prepared,
                max_tokens=max_tokens or self._settings.llm_max_output_tokens,
                temperature=self._settings.llm_temperature if temperature is None else temperature,
            )
        except ProviderError as exc:
            with self._lock:
                self._failures += 1
            LOGGER.warning("llm_call_failed", extra={"purpose": purpose, "error": str(exc)})
            return ""
        with self._lock:
            self._calls += 1
            self._tokens_in += tokens_in
            self._tokens_out += self._provider.estimate_tokens(output)
        return output

    def classify(
        self,
        *,
        system: str,
        user_text: str,
        labels: Sequence[str],
        purpose: str = "intent",
    ) -> tuple[str | None, float]:
        """Constrained classification. Returns ``(label, confidence)``.

        The deterministic rule classifier runs first everywhere in this
        platform; this method only ever *refines* a low-confidence result, and
        an unparseable or out-of-vocabulary answer is discarded rather than
        guessed at.
        """
        if self.is_local:
            return None, 0.0
        instruction = (
            f"{system}\n\nRespond with exactly one label from this list and nothing else:\n"
            + "\n".join(labels)
        )
        raw = self.complete(
            system=instruction,
            messages=[{"role": "user", "content": user_text}],
            max_tokens=16,
            temperature=0.0,
            purpose=purpose,
        )
        candidate = (raw or "").strip().splitlines()[0].strip() if raw else ""
        candidate = candidate.strip("`\"' .")
        if candidate in labels:
            return candidate, 0.75
        for label in labels:
            if candidate.upper() == label.upper():
                return label, 0.7
        return None, 0.0

    def usage(self) -> dict[str, Any]:
        with self._lock:
            return {
                "provider": self.provider_name,
                "model": self._settings.llm_model,
                "calls": self._calls,
                "failures": self._failures,
                "tokens_in": self._tokens_in,
                "tokens_out": self._tokens_out,
                "daily_budget": self._settings.llm_daily_token_budget,
            }

    def _budget_exceeded(self) -> bool:
        with self._lock:
            return (self._tokens_in + self._tokens_out) >= self._settings.llm_daily_token_budget
