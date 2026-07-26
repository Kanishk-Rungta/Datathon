"""SupervisorAgent — the conversation controller.

Owns one turn end to end:

1. detect and normalise language (deterministic service);
2. load session memory and resolve anaphora ("that case", "him") into ids;
3. classify intent and extract slots (rules first, model only when unsure);
4. route to one or more of the four specialist agents;
5. hand every result to the deterministic answer composer, which enforces the
   evidence rule and then, optionally, asks the LLM to smooth the prose and
   verifies that the smoothing changed no fact;
6. render back into the user's language, persist the turn, and audit.

The supervisor never produces a fact of its own. If no specialist returns an
evidence-bound claim, the turn honestly reports that.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

from ...domain.enums import AgentName, Intent, Language, Permission
from ...domain.errors import AuthorizationError, CIPError
from ...domain.models import AgentResult, Answer, Principal
from ...infrastructure.observability import get_logger
from ..nlu import NLUEngine
from ..services.audit import AuditService
from ..services.authorization import AuthorizationService
from ..services.evidence import AnswerComposer, claim, merge_results
from ..services.language import ConversationLanguageService, InboundText
from ..services.memory import MemoryService
from .base import AgentRequest, BaseAgent
from .crime_analytics import CrimeAnalyticsAgent
from .data_retrieval import DataRetrievalAgent
from .investigation_support import InvestigationSupportAgent
from .network_intelligence import NetworkIntelligenceAgent

LOGGER = get_logger(__name__)

#: Which agent owns which intent. Exhaustive by construction: a new Intent
#: without an entry raises at startup rather than silently defaulting.
INTENT_ROUTING: dict[Intent, AgentName] = {
    Intent.LOOKUP_CASE: AgentName.DATA_RETRIEVAL,
    Intent.LOOKUP_PERSON: AgentName.DATA_RETRIEVAL,
    Intent.LOOKUP_LOCATION: AgentName.DATA_RETRIEVAL,
    Intent.SIMILAR_CASE: AgentName.DATA_RETRIEVAL,
    Intent.TREND_QUERY: AgentName.CRIME_ANALYTICS,
    Intent.SEASONAL_QUERY: AgentName.CRIME_ANALYTICS,
    Intent.HOTSPOT_QUERY: AgentName.CRIME_ANALYTICS,
    Intent.DEMOGRAPHIC_INSIGHT: AgentName.CRIME_ANALYTICS,
    Intent.EARLY_WARNING: AgentName.CRIME_ANALYTICS,
    Intent.NETWORK_QUERY: AgentName.NETWORK_INTELLIGENCE,
    Intent.OFFENDER_PROFILE: AgentName.NETWORK_INTELLIGENCE,
    Intent.FINANCIAL_LINK: AgentName.NETWORK_INTELLIGENCE,
    Intent.INVESTIGATION_SUMMARY: AgentName.INVESTIGATION_SUPPORT,
    Intent.GENERAL_QA: AgentName.DATA_RETRIEVAL,
}

#: Intents where a second agent adds genuine value, run only when the first
#: agent returned something to build on.
FOLLOW_UP_ROUTING: dict[Intent, AgentName] = {
    Intent.INVESTIGATION_SUMMARY: AgentName.NETWORK_INTELLIGENCE,
    Intent.LOOKUP_CASE: AgentName.INVESTIGATION_SUPPORT,
}

INTENT_PERMISSIONS: dict[Intent, Permission] = {
    Intent.FINANCIAL_LINK: Permission.USE_FINANCIAL_TOOLS,
    Intent.NETWORK_QUERY: Permission.USE_GRAPH_TOOLS,
    Intent.OFFENDER_PROFILE: Permission.USE_GRAPH_TOOLS,
    Intent.DEMOGRAPHIC_INSIGHT: Permission.READ_AGGREGATES,
}


@dataclass(slots=True)
class TurnRequest:
    principal: Principal
    session_id: str
    text: str
    language: Language | None = None
    want_audio: bool = False
    options: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.options is None:
            self.options = {}


class SupervisorAgent:
    name = AgentName.SUPERVISOR

    def __init__(
        self,
        *,
        nlu: NLUEngine,
        memory: MemoryService,
        language: ConversationLanguageService,
        composer: AnswerComposer,
        authorization: AuthorizationService,
        audit: AuditService,
        data_retrieval: DataRetrievalAgent,
        crime_analytics: CrimeAnalyticsAgent,
        network_intelligence: NetworkIntelligenceAgent,
        investigation_support: InvestigationSupportAgent,
        clock: Any,
    ) -> None:
        self._nlu = nlu
        self._memory = memory
        self._language = language
        self._composer = composer
        self._authorization = authorization
        self._audit = audit
        self._clock = clock
        self._agents: dict[AgentName, BaseAgent] = {
            AgentName.DATA_RETRIEVAL: data_retrieval,
            AgentName.CRIME_ANALYTICS: crime_analytics,
            AgentName.NETWORK_INTELLIGENCE: network_intelligence,
            AgentName.INVESTIGATION_SUPPORT: investigation_support,
        }
        missing = [intent for intent in Intent if intent not in INTENT_ROUTING]
        if missing:  # pragma: no cover - guarded at import time
            raise RuntimeError(f"Intents without a route: {missing}")

    # ----------------------------------------------------------- public API
    def handle_turn(self, request: TurnRequest) -> Answer:
        started = time.perf_counter()
        inbound: InboundText = self._language.to_english(request.text, declared=request.language)
        state = self._memory.load(request.session_id, request.principal.user_id)
        if inbound.language is not Language.ENGLISH:
            state.language = inbound.language

        nlu_result = self._nlu.classify(inbound.english)
        slots, memory_notes = self._memory.resolve_coreference(inbound.english, nlu_result.slots, state)
        nlu_result.slots = slots
        scope = request.principal.scope

        agent_request = AgentRequest(
            principal=request.principal,
            intent=nlu_result.intent,
            slots=slots,
            scope=scope,
            text_english=inbound.english,
            session_id=request.session_id,
            today=self._today(),
            pinned_case_master_ids=list(state.pinned_case_master_ids),
            pinned_person_names=list(state.pinned_person_names),
            memory_notes=memory_notes,
            options=dict(request.options),
        )

        results = self._route(agent_request)
        answer = self._compose(request, inbound, nlu_result, results, memory_notes)

        self._memory.append_turn(
            state,
            role=str(request.principal.role),
            user_text_original=request.text,
            user_text_english=inbound.english,
            user_language=inbound.language,
            answer_text_english=answer.answer_text,
            answer_text_display=answer.answer_text_display,
            intent=nlu_result.intent,
            evidence_locators=[item.locator for item in answer.evidence],
            payload_type=answer.payload.payload_type,
            pinned_case_master_ids=_collect_case_ids(results),
            pinned_person_names=_collect_person_names(results, slots),
            pinned_district_ids=slots.district_ids,
        )
        self._audit.record(
            action="conversation.turn",
            principal=request.principal,
            agent=str(self.name),
            object_type="session",
            object_ids=[request.session_id],
            outcome="success",
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_payload={"intent": str(nlu_result.intent), "language": str(inbound.language),
                             "confidence": nlu_result.confidence},
            detail={"agents": [str(r.agent) for r in results],
                    "evidence_count": len(answer.evidence)},
        )
        return answer

    # -------------------------------------------------------------- routing
    def _route(self, request: AgentRequest) -> list[AgentResult]:
        required = INTENT_PERMISSIONS.get(request.intent)
        if required and not request.principal.has(required):
            raise AuthorizationError(
                f"Your role does not include '{required}', which this question requires.",
                role=str(request.principal.role), permission=str(required),
            )

        primary_name = INTENT_ROUTING[request.intent]
        results: list[AgentResult] = []
        primary = self._run(primary_name, request)
        if primary is not None:
            results.append(primary)

        follow_up = FOLLOW_UP_ROUTING.get(request.intent)
        if follow_up and primary is not None and self._should_follow_up(primary, follow_up, request):
            enriched = self._enrich(request, primary)
            secondary = self._run(follow_up, enriched)
            if secondary is not None and secondary.summary_claims:
                results.append(secondary)
        return results

    def _run(self, name: AgentName, request: AgentRequest) -> AgentResult | None:
        agent = self._agents[name]
        try:
            return agent.handle(request)
        except AuthorizationError:
            raise
        except CIPError as error:
            LOGGER.warning("agent_failed", extra={"agent": str(name), "error": error.code})
            return AgentResult(
                agent=name, intent=request.intent,
                summary_claims=[claim(f"{name} could not complete this request: {error.message}")],
                confidence=0.3, warnings=[error.code],
            )

    @staticmethod
    def _should_follow_up(primary: AgentResult, follow_up: AgentName, request: AgentRequest) -> bool:
        if not primary.summary_claims or not primary.evidence:
            return False
        case_ids = primary.data.get("case_master_ids") or []
        if follow_up is AgentName.INVESTIGATION_SUPPORT:
            # Only brief when the user clearly meant one case.
            return len(set(case_ids)) == 1 and bool(request.slots.crime_nos or request.slots.case_master_ids)
        if follow_up is AgentName.NETWORK_INTELLIGENCE:
            # Only from a briefing that actually named accused on one case.
            return (
                primary.agent is AgentName.INVESTIGATION_SUPPORT
                and bool(primary.data.get("person_names"))
                and len(set(case_ids)) == 1
            )
        return False

    @staticmethod
    def _enrich(request: AgentRequest, primary: AgentResult) -> AgentRequest:
        slots = request.slots.model_copy(deep=True)
        case_ids = [int(c) for c in (primary.data.get("case_master_ids") or [])][:1]
        names = [str(n) for n in (primary.data.get("person_names") or [])][:1]
        if case_ids and not slots.case_master_ids:
            slots.case_master_ids = case_ids
        if names and not slots.person_names:
            slots.person_names = names
        follow_intent = (
            Intent.NETWORK_QUERY if primary.agent is AgentName.INVESTIGATION_SUPPORT
            else Intent.INVESTIGATION_SUMMARY
        )
        return AgentRequest(
            principal=request.principal, intent=follow_intent, slots=slots, scope=request.scope,
            text_english=request.text_english, session_id=request.session_id, today=request.today,
            pinned_case_master_ids=case_ids or request.pinned_case_master_ids,
            pinned_person_names=names or request.pinned_person_names,
            memory_notes=request.memory_notes, options=request.options,
        )

    # ------------------------------------------------------------ composing
    def _compose(
        self,
        request: TurnRequest,
        inbound: InboundText,
        nlu_result: Any,
        results: Sequence[AgentResult],
        memory_notes: Sequence[str],
    ) -> Answer:
        if not results or not any(result.summary_claims for result in results):
            return Answer(
                answer_text=(
                    "I could not find anything in the indexed records that answers that, and I will not guess. "
                    "Try naming a district, a crime type, a CrimeNo or a person."
                ),
                answer_text_display=self._language.from_english(
                    "I could not find anything in the indexed records that answers that, and I will not guess. "
                    "Try naming a district, a crime type, a CrimeNo or a person.",
                    target=inbound.language,
                ),
                language=inbound.language,
                intent=nlu_result.intent,
                confidence=nlu_result.confidence,
                agents_used=[result.agent for result in results],
            )

        merged = merge_results(results)
        answer = self._composer.compose(
            merged,
            intent=nlu_result.intent,
            confidence=min(nlu_result.confidence, min(r.confidence for r in results)),
            agents_used=[result.agent for result in results],
            memory_notes=list(memory_notes),
        )
        answer.language = inbound.language
        answer.answer_text_display = (
            answer.answer_text if inbound.language is Language.ENGLISH
            else self._language.from_english(answer.answer_text, target=inbound.language)
        )
        return answer

    def _today(self) -> date:
        return self._clock.now().date()


def _collect_case_ids(results: Sequence[AgentResult]) -> list[int]:
    """Case ids the turn actually reported, so follow-ups can say "that case"."""
    collected: list[int] = []
    for result in results:
        for case_id in result.data.get("case_master_ids", []) or []:
            value = int(case_id)
            if value not in collected:
                collected.append(value)
    return collected[:50]


def _collect_person_names(results: Sequence[AgentResult], slots: Any) -> list[str]:
    names: list[str] = list(getattr(slots, "person_names", []) or [])
    for result in results:
        for name in result.data.get("person_names", []) or []:
            if name not in names:
                names.append(str(name))
        subject = result.data.get("person_name")
        if subject and subject not in names:
            names.append(str(subject))
    return names[:10]
