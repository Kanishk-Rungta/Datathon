"""Request and response models for the HTTP interface.

These are deliberately separate from the domain models. The domain owns the
invariants; these own the wire format, so a rename in one does not silently
change the other. Every response that carries a factual statement also carries
its evidence and its computation trace — that is enforced by the shape of
:class:`AnswerResponse`, not by convention.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from ...domain.enums import Language


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)


class PrincipalResponse(BaseModel):
    user_id: str
    username: str
    display_name: str
    role: str
    permissions: list[str]
    home_unit_id: int | None = None
    district_id: int | None = None
    scope_summary: str
    scope_unit_count: int


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    principal: PrincipalResponse


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(min_length=1, max_length=80)
    language: Language | None = None
    want_audio: bool = False
    options: dict[str, Any] = Field(default_factory=dict)


class EvidenceResponse(BaseModel):
    kind: str
    locator: str
    label: str
    case_master_ids: list[int] = Field(default_factory=list)
    crime_nos: list[str] = Field(default_factory=list)
    provenance: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ClaimResponse(BaseModel):
    text: str
    evidence_locators: list[str] = Field(default_factory=list)
    provenance: str


class TraceResponse(BaseModel):
    operation: str
    description: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    row_count: int | None = None
    formula: str | None = None
    components: list[dict[str, Any]] = Field(default_factory=list)


class PayloadResponse(BaseModel):
    payload_type: str
    title: str
    data: dict[str, Any] = Field(default_factory=dict)


class AnswerResponse(BaseModel):
    answer_text: str
    answer_text_display: str
    language: str
    intent: str
    confidence: float
    agents_used: list[str]
    claims: list[ClaimResponse]
    evidence: list[EvidenceResponse]
    traces: list[TraceResponse]
    payload: PayloadResponse
    needs_clarification: str | None = None
    warnings: list[str] = Field(default_factory=list)
    audio_url: str | None = None
    session_id: str


class TranscriptTurn(BaseModel):
    turn_seq: int
    created_at: str
    user_text_original: str
    user_text_english: str
    answer_text_display: str
    intent: str
    evidence_locators: list[str] = Field(default_factory=list)


class CaseSearchRequest(BaseModel):
    district_ids: list[int] = Field(default_factory=list)
    unit_ids: list[int] = Field(default_factory=list)
    crime_sub_head_ids: list[int] = Field(default_factory=list)
    status_ids: list[int] = Field(default_factory=list)
    crime_nos: list[str] = Field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    limit: int = Field(default=25, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class TrendRequest(BaseModel):
    district_ids: list[int] = Field(default_factory=list)
    unit_ids: list[int] = Field(default_factory=list)
    crime_sub_head_ids: list[int] = Field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None


class HotspotRequest(BaseModel):
    district_ids: list[int] = Field(default_factory=list)
    crime_sub_head_ids: list[int] = Field(default_factory=list)
    window_days: int = Field(default=90, ge=7, le=730)


class SociologyRequest(BaseModel):
    dimension: Literal["occupation", "age_band", "gender", "religion", "caste"] = "occupation"
    district_ids: list[int] = Field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None


class GraphExpandRequest(BaseModel):
    node_id: str | None = None
    person_name: str | None = None
    case_master_id: int | None = None
    hops: int = Field(default=2, ge=1, le=3)
    edge_types: list[str] = Field(default_factory=list)
    max_nodes: int = Field(default=120, ge=10, le=400)


class GraphPathRequest(BaseModel):
    from_person: str
    to_person: str


class ExportRequest(BaseModel):
    session_id: str | None = None
    case_master_id: int | None = None
    title: str | None = None


class ExportResponse(BaseModel):
    url: str
    key: str
    filename: str
    bytes: int
    kannada_glyphs_embedded: bool
    notice: str


class SeedRequest(BaseModel):
    target_cases: int = Field(default=4200, ge=100, le=40_000)
    months: int = Field(default=30, ge=6, le=120)
    reset: bool = False


class ReviewDecisionRequest(BaseModel):
    link_id: str
    decision: Literal["confirmed", "rejected"]


class TranscribeResponse(BaseModel):
    text: str
    language: str
    provider: str
    is_full_fidelity: bool
    notice: str | None = None
