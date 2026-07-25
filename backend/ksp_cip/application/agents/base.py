"""The agent contract.

Exactly five agents exist (``AgentName``). Everything else in the application
layer is a deterministic service. An agent:

* receives an :class:`AgentRequest` carrying the authenticated principal, the
  resolved intent, the extracted slots and the authorized scope;
* calls deterministic services and repositories to obtain facts;
* returns an :class:`AgentResult` whose every claim is bound to evidence.

An agent never formats final prose (the composer does), never calls an LLM for
a fact, and never widens its own scope.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ...domain.enums import AgentName, Intent
from ...domain.models import AgentResult, Principal, Slots, UnitScope
from ..services.audit import AuditService


@dataclass(slots=True)
class AgentRequest:
    principal: Principal
    intent: Intent
    slots: Slots
    scope: UnitScope
    text_english: str
    session_id: str
    today: date
    pinned_case_master_ids: list[int] = field(default_factory=list)
    pinned_person_names: list[str] = field(default_factory=list)
    memory_notes: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return {
            "intent": str(self.intent),
            "slots": self.slots.model_dump(mode="json"),
            "text_english": self.text_english[:400],
            "session_id": self.session_id,
            "today": self.today.isoformat(),
            "pinned_case_master_ids": self.pinned_case_master_ids[:20],
            "options": self.options,
        }


class BaseAgent(ABC):
    name: AgentName

    def __init__(self, audit: AuditService) -> None:
        self.audit = audit

    @abstractmethod
    def handle(self, request: AgentRequest) -> AgentResult:  # pragma: no cover - abstract
        ...

    def empty_result(self, request: AgentRequest, message: str, *, clarification: str | None = None) -> AgentResult:
        from ..services.evidence import claim

        return AgentResult(
            agent=self.name,
            intent=request.intent,
            summary_claims=[claim(message)],
            confidence=0.5,
            needs_clarification=clarification,
        )
