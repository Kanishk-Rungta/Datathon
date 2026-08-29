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

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ...domain.enums import AgentName, Intent
from ...domain.models import AgentResult, Principal, Slots, UnitScope
from ..services.audit import AuditService

#: Requests to say what a *person* will do next.
#:
#: This lives in the shared agent contract, not in one agent, because the same
#: prohibited question classifies onto several intents — "predict which person
#: will offend" as FORECAST_QUERY, "which accused will offend next month" as
#: TREND_QUERY, "who will commit a crime next year" as GENERAL_QA. The
#: supervisor checks it before routing so the refusal cannot be reached around,
#: and the forecast agent checks person *slots* separately for the case where a
#: name was extracted but the phrasing is not future-tense.
#:
#: Every branch requires a **forward-looking verb**, never just a person noun.
#: Without that requirement the pattern blocked "which accused are named in FIR
#: 104430006202600001?" — a plain recorded-fact question — because "which
#: accused" alone was enough to trip it. Asking what a person *did* must stay
#: answerable; only asking what they *will do* is refused.
INDIVIDUAL_PREDICTION_RE = re.compile(
    # "which person will …", "which of these accused will …"
    r"\b(?:which|what|who)\s+(?:of\s+(?:these|those|the)\s+)?"
    r"(?:person|people|individual|individuals|accused|offender|offenders|suspect|suspects|one)\b"
    r"[^.?]{0,40}?\b(?:will|would|is going to|are going to|is likely to|are likely to|might|may)\b"
    # "who will …"
    r"|\bwho\s+(?:will|would|is likely to|is going to|might)\b"
    # "will X reoffend", "is likely to commit"
    r"|\b(?:will|would|is likely to|are likely to)\s+(?:\w+\s+){0,3}(?:commit|reoffend|re-offend|offend)\b"
    # "probability that X commits", "risk of X reoffending"
    r"|\b(?:likelihood|probability|chance|risk)\s+(?:that|of)\s+[^.?]{0,40}?(?:commit|reoffend|offend)",
    flags=re.IGNORECASE,
)


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
