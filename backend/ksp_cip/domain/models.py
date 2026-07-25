"""Domain entities and the answer contract.

Nothing in this module performs I/O. The single most important type here is
:class:`Evidence`: no factual claim may leave the platform without one.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .enums import AgentName, EdgeType, EvidenceKind, Intent, Language, NodeType, Permission, Provenance, Role


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False, populate_by_name=True)


# ---------------------------------------------------------------- principals


class UnitScope(DomainModel):
    """The subtree of police units a principal may see (architecture §12.2)."""

    root_unit_id: int | None = None
    unit_ids: frozenset[int] = Field(default_factory=frozenset)
    statewide: bool = False

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    def allows(self, unit_id: int | None) -> bool:
        if self.statewide:
            return True
        if unit_id is None:
            return False
        return unit_id in self.unit_ids


class Principal(DomainModel):
    user_id: str
    username: str
    display_name: str
    role: Role
    home_unit_id: int | None = None
    district_id: int | None = None
    permissions: frozenset[Permission] = Field(default_factory=frozenset)
    scope: UnitScope = Field(default_factory=UnitScope)

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions

    def require(self, permission: Permission) -> None:
        from .errors import AuthorizationError

        if not self.has(permission):
            raise AuthorizationError(
                f"Role '{self.role}' lacks permission '{permission}'",
                role=str(self.role),
                permission=str(permission),
            )


# ------------------------------------------------------------------ evidence


class Evidence(DomainModel):
    """A traceable pointer back to authoritative source records.

    ``locator`` is what an officer types into the FIR system to verify the
    claim: a CrimeNo for case-level evidence, an aggregate descriptor for
    computed figures.
    """

    kind: EvidenceKind
    locator: str
    label: str
    case_master_ids: list[int] = Field(default_factory=list)
    crime_nos: list[str] = Field(default_factory=list)
    provenance: Provenance = Provenance.SOURCE_RECORD
    detail: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_inferred(self) -> bool:
        return self.provenance is Provenance.INFERRED


class Claim(DomainModel):
    """One sentence of an answer, bound to the evidence that supports it."""

    text: str
    evidence_locators: list[str] = Field(default_factory=list)
    provenance: Provenance = Provenance.SOURCE_RECORD

    @property
    def requires_evidence(self) -> bool:
        return self.provenance is not Provenance.INFERRED or bool(self.evidence_locators)


class ComputationTrace(DomainModel):
    """The "how I got this" line demanded by the explainability requirement."""

    operation: str
    description: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    row_count: int | None = None
    formula: str | None = None
    components: list[dict[str, Any]] = Field(default_factory=list)


class StructuredPayload(DomainModel):
    """Chart/graph/table spec consumed directly by the React client."""

    payload_type: Literal["none", "line", "bar", "heatmap", "graph", "table", "timeline", "score", "map"] = "none"
    title: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class AgentResult(DomainModel):
    """Uniform return type of every one of the five agents."""

    agent: AgentName
    intent: Intent
    summary_claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    traces: list[ComputationTrace] = Field(default_factory=list)
    payload: StructuredPayload = Field(default_factory=StructuredPayload)
    confidence: float = 1.0
    needs_clarification: str | None = None
    warnings: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class Answer(DomainModel):
    """What the API returns for a conversational turn."""

    answer_text: str
    answer_text_display: str = ""
    language: Language = Language.ENGLISH
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    traces: list[ComputationTrace] = Field(default_factory=list)
    payload: StructuredPayload = Field(default_factory=StructuredPayload)
    agents_used: list[AgentName] = Field(default_factory=list)
    intent: Intent = Intent.GENERAL_QA
    confidence: float = 1.0
    needs_clarification: str | None = None
    warnings: list[str] = Field(default_factory=list)
    audio_url: str | None = None


# ------------------------------------------------------------------- NLU


class Slots(DomainModel):
    district_names: list[str] = Field(default_factory=list)
    district_ids: list[int] = Field(default_factory=list)
    unit_names: list[str] = Field(default_factory=list)
    unit_ids: list[int] = Field(default_factory=list)
    crime_types: list[str] = Field(default_factory=list)
    crime_sub_head_ids: list[int] = Field(default_factory=list)
    person_names: list[str] = Field(default_factory=list)
    crime_nos: list[str] = Field(default_factory=list)
    case_master_ids: list[int] = Field(default_factory=list)
    act_sections: list[str] = Field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    relative_period_days: int | None = None
    status_names: list[str] = Field(default_factory=list)
    limit: int | None = None
    #: Terms the user named that resolved to nothing in the master data. These
    #: are surfaced rather than dropped: silently ignoring "in Kathmandu" turns
    #: a narrow question into a broad one without telling the officer.
    unresolved_terms: list[str] = Field(default_factory=list)
    free_text: str = ""

    def is_empty(self) -> bool:
        dumped = self.model_dump(exclude={"free_text"})
        return not any(bool(value) for value in dumped.values())


class NLUResult(DomainModel):
    intent: Intent
    slots: Slots
    confidence: float
    method: Literal["rules", "llm", "rules+llm", "memory"] = "rules"
    alternatives: list[Intent] = Field(default_factory=list)


# ------------------------------------------------------------------- case


class CaseSummary(DomainModel):
    case_master_id: int
    crime_no: str
    case_no: str | None = None
    crime_registered_date: date | None = None
    incident_from_date: datetime | None = None
    incident_to_date: datetime | None = None
    info_received_ps_date: datetime | None = None
    police_station_id: int | None = None
    police_station_name: str | None = None
    district_id: int | None = None
    district_name: str | None = None
    case_category: str | None = None
    gravity: str | None = None
    crime_head: str | None = None
    crime_sub_head: str | None = None
    crime_major_head_id: int | None = None
    crime_minor_head_id: int | None = None
    status: str | None = None
    court_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    brief_facts: str | None = None
    brief_facts_kn: str | None = None


class PersonRecord(DomainModel):
    role: Literal["accused", "victim", "complainant"]
    record_id: int
    case_master_id: int
    crime_no: str
    name: str
    age_year: int | None = None
    gender_id: str | None = None
    person_ref: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class GraphNode(DomainModel):
    node_id: str
    node_type: NodeType
    label: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class GraphLink(DomainModel):
    source: str
    target: str
    edge_type: EdgeType
    weight: float = 1.0
    case_master_ids: list[int] = Field(default_factory=list)
    crime_nos: list[str] = Field(default_factory=list)
    provenance: Provenance = Provenance.INFERRED


class GraphView(DomainModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    links: list[GraphLink] = Field(default_factory=list)
    communities: dict[str, int] = Field(default_factory=dict)
    centrality: dict[str, float] = Field(default_factory=dict)


class ConversationTurn(DomainModel):
    session_id: str
    turn_seq: int
    user_id: str
    role: Role
    created_at: datetime
    user_text_original: str
    user_text_english: str
    user_language: Language
    answer_text_english: str
    answer_text_display: str
    intent: Intent
    evidence_locators: list[str] = Field(default_factory=list)
    pinned_case_master_ids: list[int] = Field(default_factory=list)
    pinned_person_names: list[str] = Field(default_factory=list)
    payload_type: str = "none"


class SessionState(DomainModel):
    session_id: str
    user_id: str
    language: Language = Language.ENGLISH
    pinned_case_master_ids: list[int] = Field(default_factory=list)
    pinned_person_names: list[str] = Field(default_factory=list)
    pinned_district_ids: list[int] = Field(default_factory=list)
    last_intent: Intent | None = None
    updated_at: datetime | None = None
