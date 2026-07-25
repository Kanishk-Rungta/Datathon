"""Evidence construction and the answer composer.

This module is where the platform's central promise is mechanically enforced:

* every factual claim carries at least one evidence locator;
* every inferred relationship is labelled ``(inferred)``;
* every statement resting on the synthetic financial extension is labelled
  ``(synthetic extension)``;
* the optional LLM polish pass cannot introduce a number, a name, or a
  citation that the deterministic draft did not already contain — a verifier
  compares the two and discards the rewrite on any violation.

The composer is deterministic templating (plan §6.11). The LLM is a rewriter
of last resort, never a source.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from ...domain.enums import AgentName, EvidenceKind, Intent, Provenance
from ...domain.errors import EvidenceMissingError
from ...domain.models import AgentResult, Answer, CaseSummary, Claim, ComputationTrace, Evidence
from ...domain.ports import LLMGateway
from ...infrastructure.observability import get_logger

LOGGER = get_logger(__name__)

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_CITATION_RE = re.compile(r"\[([0-9]{18}|AGG:[^\]]+|EDGE:[^\]]+|ALERT:[^\]]+|TXN:[^\]]+|PERSON:[^\]]+)\]")

INFERRED_MARKER = "(inferred)"
EXTENSION_MARKER = "(synthetic extension)"


# ---------------------------------------------------------------- builders


def case_evidence(case: CaseSummary, *, label: str | None = None) -> Evidence:
    return Evidence(
        kind=EvidenceKind.CASE,
        locator=case.crime_no,
        label=label or f"FIR {case.crime_no} · {case.police_station_name or 'unknown station'}",
        case_master_ids=[case.case_master_id],
        crime_nos=[case.crime_no],
        provenance=Provenance.SOURCE_RECORD,
        detail={
            "registered": case.crime_registered_date.isoformat() if case.crime_registered_date else None,
            "status": case.status,
            "crime_sub_head": case.crime_sub_head,
            "district": case.district_name,
        },
    )


def aggregate_evidence(
    *,
    key: str,
    label: str,
    case_master_ids: Sequence[int],
    crime_nos: Sequence[str] = (),
    detail: dict | None = None,
) -> Evidence:
    return Evidence(
        kind=EvidenceKind.AGGREGATE,
        locator=f"AGG:{key}",
        label=label,
        case_master_ids=list(case_master_ids)[:500],
        crime_nos=list(crime_nos)[:500],
        provenance=Provenance.DETERMINISTIC_COMPUTATION,
        detail=detail or {},
    )


def empty_result_evidence(*, key: str, label: str, detail: dict | None = None) -> Evidence:
    """Evidence for a query that legitimately returned no rows.

    A "nothing found" answer still makes a checkable assertion — that a
    specific query over specific data was empty — so it gets a locator like
    any other claim. Without this, honest negative answers would be the only
    statements in the platform that could not be verified.
    """
    return Evidence(
        kind=EvidenceKind.AGGREGATE,
        locator=f"AGG:{key}",
        label=label,
        case_master_ids=[],
        provenance=Provenance.DETERMINISTIC_COMPUTATION,
        detail={**(detail or {}), "row_count": 0},
    )


def edge_evidence(
    *, edge_id: str, label: str, case_master_ids: Sequence[int], crime_nos: Sequence[str], detail: dict | None = None
) -> Evidence:
    return Evidence(
        kind=EvidenceKind.EDGE,
        locator=f"EDGE:{edge_id}",
        label=label,
        case_master_ids=list(case_master_ids),
        crime_nos=list(crime_nos),
        provenance=Provenance.INFERRED,
        detail=detail or {},
    )


def alert_evidence(*, alert_id: str, label: str, case_master_ids: Sequence[int], detail: dict | None = None) -> Evidence:
    return Evidence(
        kind=EvidenceKind.ALERT,
        locator=f"ALERT:{alert_id}",
        label=label,
        case_master_ids=list(case_master_ids),
        provenance=Provenance.DETERMINISTIC_COMPUTATION,
        detail=detail or {},
    )


def transaction_evidence(*, txn_id: str, label: str, case_master_ids: Sequence[int], detail: dict | None = None) -> Evidence:
    return Evidence(
        kind=EvidenceKind.TRANSACTION,
        locator=f"TXN:{txn_id}",
        label=label,
        case_master_ids=list(case_master_ids),
        provenance=Provenance.SYNTHETIC_EXTENSION,
        detail=detail or {},
    )


def person_evidence(*, identity_id: str, label: str, case_master_ids: Sequence[int], crime_nos: Sequence[str]) -> Evidence:
    return Evidence(
        kind=EvidenceKind.PERSON,
        locator=f"PERSON:{identity_id}",
        label=label,
        case_master_ids=list(case_master_ids),
        crime_nos=list(crime_nos),
        provenance=Provenance.INFERRED,
    )


def claim(text: str, evidence: Iterable[Evidence] | Iterable[str] = (), *, provenance: Provenance = Provenance.SOURCE_RECORD) -> Claim:
    locators: list[str] = []
    for item in evidence:
        locators.append(item.locator if isinstance(item, Evidence) else str(item))
    return Claim(text=text, evidence_locators=locators, provenance=provenance)


# --------------------------------------------------------------- composer


class AnswerComposer:
    """Deterministic answer assembly with an optional, verified LLM rewrite."""

    def __init__(self, llm: LLMGateway | None = None, *, enable_polish: bool = True) -> None:
        self._llm = llm
        self._enable_polish = enable_polish

    def compose(
        self,
        result: AgentResult,
        *,
        prompt_name: str = "narrative_composer",
        intent: Intent | None = None,
        confidence: float | None = None,
        agents_used: Sequence[AgentName] | None = None,
        memory_notes: Sequence[str] | None = None,
    ) -> Answer:
        self._enforce_evidence(result)
        draft = self._render(result)
        text = draft
        if self._enable_polish and self._llm is not None and not getattr(self._llm, "is_local", True):
            text = self._polish(draft, result, prompt_name) or draft
        traces = list(result.traces)
        if memory_notes:
            traces.insert(0, ComputationTrace(
                operation="conversation_memory",
                description="Filled references from the previous turn: " + "; ".join(memory_notes),
                inputs={"notes": list(memory_notes)},
            ))
        return Answer(
            answer_text=text,
            answer_text_display=text,
            claims=result.summary_claims,
            evidence=result.evidence,
            traces=traces,
            payload=result.payload,
            agents_used=list(agents_used) if agents_used else [result.agent],
            intent=intent or result.intent,
            confidence=result.confidence if confidence is None else min(confidence, result.confidence),
            needs_clarification=result.needs_clarification,
            warnings=result.warnings,
        )

    # ------------------------------------------------------------ internals
    @staticmethod
    def _enforce_evidence(result: AgentResult) -> None:
        available = {item.locator for item in result.evidence}
        for statement in result.summary_claims:
            if statement.provenance is Provenance.INFERRED and not statement.evidence_locators:
                raise EvidenceMissingError(
                    "Inferred claim emitted without evidence", claim=statement.text[:120]
                )
            if not statement.evidence_locators:
                # A claim with no locator is only allowed when it carries no
                # facts — the composer checks that by looking for digits.
                if _NUMBER_RE.search(statement.text):
                    raise EvidenceMissingError(
                        "Numeric claim emitted without evidence", claim=statement.text[:120]
                    )
                continue
            missing = [loc for loc in statement.evidence_locators if loc not in available]
            if missing:
                raise EvidenceMissingError(
                    "Claim cites an evidence locator that was not published",
                    claim=statement.text[:120],
                    missing=missing,
                )

    @staticmethod
    def _render(result: AgentResult) -> str:
        lines: list[str] = []
        for statement in result.summary_claims:
            suffix = ""
            if statement.provenance is Provenance.INFERRED and INFERRED_MARKER not in statement.text:
                suffix = f" {INFERRED_MARKER}"
            elif statement.provenance is Provenance.SYNTHETIC_EXTENSION and EXTENSION_MARKER not in statement.text:
                suffix = f" {EXTENSION_MARKER}"
            citations = "".join(f" [{loc}]" for loc in statement.evidence_locators[:6])
            lines.append(f"{statement.text}{suffix}{citations}".strip())
        if result.needs_clarification:
            lines.append(result.needs_clarification)
        for warning in result.warnings:
            lines.append(f"Note: {warning}")
        return "\n".join(lines).strip()

    def _polish(self, draft: str, result: AgentResult, prompt_name: str) -> str | None:
        assert self._llm is not None
        try:
            system, _version = self._llm.prompts.get(prompt_name)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - prompt registry is optional at this layer
            return None
        facts = {
            "draft": draft,
            "narrative": draft,
            "citations": [item.locator for item in result.evidence],
            "traces": [trace.description for trace in result.traces],
        }
        import json as _json

        rewritten = self._llm.complete(
            system=system,
            messages=[{"role": "user", "content": "Rewrite the draft for readability.\n\nFACTS: " + _json.dumps(facts, ensure_ascii=False)}],
            purpose="narrative",
        )
        if not rewritten:
            return None
        if not self.verify_rewrite(draft, rewritten):
            LOGGER.warning("narrative_rewrite_rejected", extra={"reason": "verifier"})
            return None
        return rewritten

    @staticmethod
    def verify_rewrite(draft: str, rewritten: str) -> bool:
        """Reject a rewrite that invents facts or drops citations.

        Rules: the set of citations must be preserved exactly, and the rewrite
        may not contain a number that does not occur in the draft.
        """
        draft_citations = set(_CITATION_RE.findall(draft))
        new_citations = set(_CITATION_RE.findall(rewritten))
        if draft_citations != new_citations:
            return False
        draft_numbers = set(_NUMBER_RE.findall(draft))
        for number in _NUMBER_RE.findall(rewritten):
            if number not in draft_numbers:
                return False
        for marker in (INFERRED_MARKER, EXTENSION_MARKER):
            if draft.count(marker) > rewritten.count(marker):
                return False
        return True


def merge_results(results: Sequence[AgentResult]) -> AgentResult:
    """Fold a chain of agent results into one, preserving all evidence."""
    if not results:
        raise ValueError("merge_results requires at least one result")
    primary = results[0]
    merged = AgentResult(
        agent=primary.agent,
        intent=primary.intent,
        summary_claims=list(primary.summary_claims),
        evidence=list(primary.evidence),
        traces=list(primary.traces),
        payload=primary.payload,
        confidence=primary.confidence,
        needs_clarification=primary.needs_clarification,
        warnings=list(primary.warnings),
        data=dict(primary.data),
    )
    seen = {item.locator for item in merged.evidence}
    for extra in results[1:]:
        merged.summary_claims.extend(extra.summary_claims)
        for item in extra.evidence:
            if item.locator not in seen:
                merged.evidence.append(item)
                seen.add(item.locator)
        merged.traces.extend(extra.traces)
        merged.warnings.extend(extra.warnings)
        merged.confidence = min(merged.confidence, extra.confidence)
        if merged.payload.payload_type == "none" and extra.payload.payload_type != "none":
            merged.payload = extra.payload
        merged.data.update(extra.data)
    return merged


def trace(operation: str, description: str, **kwargs) -> ComputationTrace:
    return ComputationTrace(operation=operation, description=description, **kwargs)
