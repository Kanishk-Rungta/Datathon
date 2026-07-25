"""The conversational endpoint — the platform's primary interface."""

from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, UploadFile, File, Form

from ....application.agents import TurnRequest
from ....domain.enums import Language
from ....domain.models import Answer
from ..deps import ContainerDep, PrincipalDep
from ..schemas import (
    AnswerResponse,
    ChatRequest,
    ClaimResponse,
    EvidenceResponse,
    PayloadResponse,
    TraceResponse,
    TranscribeResponse,
    TranscriptTurn,
)

router = APIRouter(prefix="/chat", tags=["chat"])


def to_response(answer: Answer, session_id: str) -> AnswerResponse:
    return AnswerResponse(
        answer_text=answer.answer_text,
        answer_text_display=answer.answer_text_display or answer.answer_text,
        language=str(answer.language),
        intent=str(answer.intent),
        confidence=round(answer.confidence, 3),
        agents_used=[str(agent) for agent in answer.agents_used],
        claims=[
            ClaimResponse(
                text=claim.text,
                evidence_locators=claim.evidence_locators,
                provenance=str(claim.provenance),
            )
            for claim in answer.claims
        ],
        evidence=[
            EvidenceResponse(
                kind=str(item.kind), locator=item.locator, label=item.label,
                case_master_ids=item.case_master_ids, crime_nos=item.crime_nos,
                provenance=str(item.provenance), detail=item.detail,
            )
            for item in answer.evidence
        ],
        traces=[
            TraceResponse(
                operation=trace.operation, description=trace.description, inputs=trace.inputs,
                row_count=trace.row_count, formula=trace.formula, components=trace.components,
            )
            for trace in answer.traces
        ],
        payload=PayloadResponse(
            payload_type=answer.payload.payload_type,
            title=answer.payload.title,
            data=answer.payload.data,
        ),
        needs_clarification=answer.needs_clarification,
        warnings=answer.warnings,
        audio_url=answer.audio_url,
        session_id=session_id,
    )


@router.post("", response_model=AnswerResponse)
def chat(payload: ChatRequest, principal: PrincipalDep, container: ContainerDep) -> AnswerResponse:
    answer = container.supervisor.handle_turn(
        TurnRequest(
            principal=principal,
            session_id=payload.session_id,
            text=payload.message,
            language=payload.language,
            want_audio=payload.want_audio,
            options=payload.options,
        )
    )
    if payload.want_audio and answer.language is not Language.ENGLISH:
        audio = container.language.synthesize(answer.answer_text_display, language=answer.language)
        if audio:
            key = f"audio/{payload.session_id}/{abs(hash(answer.answer_text_display)) & 0xFFFFFFFF:08x}.wav"
            container.filestore.write_bytes(key, audio, content_type="audio/wav")
            answer.audio_url = container.filestore.url_for(key)
    return to_response(answer, payload.session_id)


@router.get("/{session_id}/transcript", response_model=list[TranscriptTurn])
def transcript(session_id: str, principal: PrincipalDep, container: ContainerDep) -> list[TranscriptTurn]:
    turns = container.memory.transcript(session_id)
    return [
        TranscriptTurn(
            turn_seq=turn["turn_seq"],
            created_at=str(turn["created_at"]),
            user_text_original=turn["user_text_original"],
            user_text_english=turn["user_text_english"],
            answer_text_display=turn["answer_text_display"],
            intent=turn["intent"],
            evidence_locators=turn.get("evidence_locators", []),
        )
        for turn in turns
        if turn["user_id"] == principal.user_id
    ]


@router.get("/sessions")
def sessions(principal: PrincipalDep, container: ContainerDep) -> dict[str, Any]:
    return {"sessions": container.memory.sessions(principal.user_id)}


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(
    principal: PrincipalDep,
    container: ContainerDep,
    audio: UploadFile = File(...),
    language: str = Form("kn"),
) -> TranscribeResponse:
    """Server-side speech recognition.

    The browser's Web Speech API handles voice locally and is the default. This
    endpoint exists for deployments where server-side ASR is required, and it
    tells the caller honestly when no real ASR provider is configured.
    """
    payload = await audio.read()
    target = Language(language)
    text = container.language.transcribe(
        payload, language=target, mime_type=audio.content_type or "audio/webm"
    )
    return TranscribeResponse(
        text=text,
        language=str(target),
        provider=container.language.provider_name,
        is_full_fidelity=container.language.is_full_fidelity,
        notice=(
            None if container.language.is_full_fidelity else
            "No speech recognition provider is configured on this deployment. Use the "
            "microphone button in the console, which uses your browser's own recogniser."
        ),
    )
