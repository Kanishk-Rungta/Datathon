"""Conversation memory — deterministic, auditable, exportable.

Memory is a *service*, not an agent: it performs no reasoning. It stores the
rolling turn window, maintains pinned entities per session, and resolves
coreference by rule ("that case", "those cases", "him") against the pins.
Rule-based resolution means multi-turn behaviour is unit-testable and does not
drift with a model version.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from ...domain.enums import Intent, Language
from ...domain.models import Slots
from ...domain.ports import KeyValueStore
from ...infrastructure.db.repositories import ConversationRepository

SESSION_NAMESPACE = "session_state"

_CASE_ANAPHORA = re.compile(
    r"\b(that|this|the|those|these|it|its|same)\s+(case|cases|fir|firs|crime|crimes|incident|incidents)\b",
    re.IGNORECASE,
)
_BARE_ANAPHORA = re.compile(r"\b(them|those|these|it)\b", re.IGNORECASE)
_PERSON_ANAPHORA = re.compile(r"\b(him|her|them|he|she|this person|that person|the accused)\b", re.IGNORECASE)
_DISTRICT_ANAPHORA = re.compile(r"\b(there|that district|the same district|same area)\b", re.IGNORECASE)


@dataclass(slots=True)
class SessionContext:
    session_id: str
    user_id: str
    language: Language = Language.ENGLISH
    turns: list[dict[str, Any]] = field(default_factory=list)
    pinned_case_master_ids: list[int] = field(default_factory=list)
    pinned_person_names: list[str] = field(default_factory=list)
    pinned_district_ids: list[int] = field(default_factory=list)
    last_intent: Intent | None = None

    @property
    def turn_count(self) -> int:
        return len(self.turns)


class MemoryService:
    def __init__(
        self,
        conversations: ConversationRepository,
        kv: KeyValueStore,
        *,
        window_turns: int = 8,
        ttl_days: int = 30,
    ) -> None:
        self._conversations = conversations
        self._kv = kv
        self._window = window_turns
        self._ttl_seconds = ttl_days * 86_400

    # -------------------------------------------------------------- context
    def load(self, session_id: str, user_id: str) -> SessionContext:
        turns = self._conversations.recent_turns(session_id, self._window)
        state = self._kv.get(SESSION_NAMESPACE, session_id) or {}
        return SessionContext(
            session_id=session_id,
            user_id=user_id,
            language=Language(state.get("language", Language.ENGLISH.value)),
            turns=turns,
            pinned_case_master_ids=[int(v) for v in state.get("pinned_case_master_ids", [])],
            pinned_person_names=[str(v) for v in state.get("pinned_person_names", [])],
            pinned_district_ids=[int(v) for v in state.get("pinned_district_ids", [])],
            last_intent=Intent(state["last_intent"]) if state.get("last_intent") else None,
        )

    def save_state(self, context: SessionContext) -> None:
        self._kv.put(
            SESSION_NAMESPACE,
            context.session_id,
            {
                "user_id": context.user_id,
                "language": context.language.value,
                "pinned_case_master_ids": context.pinned_case_master_ids[:50],
                "pinned_person_names": context.pinned_person_names[:20],
                "pinned_district_ids": context.pinned_district_ids[:20],
                "last_intent": context.last_intent.value if context.last_intent else None,
            },
            ttl_seconds=self._ttl_seconds,
        )

    def append_turn(
        self,
        context: SessionContext,
        *,
        role: str,
        user_text_original: str,
        user_text_english: str,
        user_language: Language,
        answer_text_english: str,
        answer_text_display: str,
        intent: Intent,
        evidence_locators: Sequence[str],
        payload_type: str,
        pinned_case_master_ids: Sequence[int],
        pinned_person_names: Sequence[str],
        pinned_district_ids: Sequence[int],
    ) -> int:
        turn_seq = self._conversations.next_turn_seq(context.session_id)
        self._conversations.append(
            {
                "session_id": context.session_id,
                "turn_seq": turn_seq,
                "user_id": context.user_id,
                "role": role,
                "user_text_original": user_text_original,
                "user_text_english": user_text_english,
                "user_language": user_language.value,
                "answer_text_english": answer_text_english,
                "answer_text_display": answer_text_display,
                "intent": intent.value,
                "evidence_locators": list(evidence_locators)[:60],
                "pinned": {
                    "case_master_ids": list(pinned_case_master_ids)[:50],
                    "person_names": list(pinned_person_names)[:20],
                    "district_ids": list(pinned_district_ids)[:20],
                },
                "payload_type": payload_type,
            }
        )
        context.pinned_case_master_ids = list(dict.fromkeys(list(pinned_case_master_ids) + context.pinned_case_master_ids))[:50]
        context.pinned_person_names = list(dict.fromkeys(list(pinned_person_names) + context.pinned_person_names))[:20]
        context.pinned_district_ids = list(dict.fromkeys(list(pinned_district_ids) + context.pinned_district_ids))[:20]
        context.last_intent = intent
        context.language = user_language
        self.save_state(context)
        return turn_seq

    # ---------------------------------------------------------- coreference
    def resolve_coreference(self, text: str, slots: Slots, context: SessionContext) -> tuple[Slots, list[str]]:
        """Fill empty slots from session pins when the text is anaphoric.

        Returns the (possibly enriched) slots and a list of human-readable
        notes describing what was resolved — these appear in the trace so the
        officer can see why the platform answered about a particular case.
        """
        notes: list[str] = []
        resolved = slots.model_copy(deep=True)

        wants_case = bool(_CASE_ANAPHORA.search(text)) or (
            bool(_BARE_ANAPHORA.search(text)) and not resolved.case_master_ids and not resolved.crime_nos
        )
        if wants_case and not resolved.case_master_ids and not resolved.crime_nos and context.pinned_case_master_ids:
            resolved.case_master_ids = list(context.pinned_case_master_ids)
            notes.append(
                f"Resolved '{_first_match(_CASE_ANAPHORA, text) or 'them'}' to "
                f"{len(resolved.case_master_ids)} case(s) referenced in the previous turn"
            )

        if _PERSON_ANAPHORA.search(text) and not resolved.person_names and context.pinned_person_names:
            resolved.person_names = list(context.pinned_person_names[:1])
            notes.append(f"Resolved the pronoun to '{resolved.person_names[0]}' from the previous turn")

        if _DISTRICT_ANAPHORA.search(text) and not resolved.district_ids and context.pinned_district_ids:
            resolved.district_ids = list(context.pinned_district_ids[:1])
            notes.append("Resolved the location reference to the district discussed in the previous turn")

        return resolved, notes

    # --------------------------------------------------------------- export
    def transcript(self, session_id: str) -> list[dict[str, Any]]:
        return self._conversations.all_turns(session_id)

    def sessions(self, user_id: str, limit: int = 25) -> list[dict[str, Any]]:
        return self._conversations.sessions_for_user(user_id, limit)

    def purge_expired(self) -> dict[str, int]:
        return {
            "conversation_turns": self._conversations.purge_expired(),
            "kv_documents": self._kv.purge_expired(),
        }


def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(0) if match else None
