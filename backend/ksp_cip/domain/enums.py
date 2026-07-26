"""Enumerations shared across the platform."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Authorization roles (plan §9, architecture §12.2).

    Ordering here is not authority ordering; capabilities are declared
    explicitly in ``application.services.authorization``.
    """

    INVESTIGATOR = "investigator"
    ANALYST = "analyst"
    SUPERVISOR = "supervisor"
    POLICYMAKER = "policymaker"
    AUDITOR = "auditor"
    PLATFORM_ADMIN = "platform_admin"


class Permission(StrEnum):
    READ_CASE_DETAIL = "read_case_detail"
    READ_PERSON_IDENTITY = "read_person_identity"
    READ_SENSITIVE_DEMOGRAPHICS = "read_sensitive_demographics"
    READ_AGGREGATES = "read_aggregates"
    READ_STATEWIDE = "read_statewide"
    USE_GRAPH_TOOLS = "use_graph_tools"
    USE_OFFENDER_PROFILING = "use_offender_profiling"
    USE_FINANCIAL_TOOLS = "use_financial_tools"
    READ_AUDIT = "read_audit"
    EXPORT_PDF = "export_pdf"
    ADMIN_PIPELINE = "admin_pipeline"


class Language(StrEnum):
    ENGLISH = "en"
    KANNADA = "kn"


class Intent(StrEnum):
    """Fixed intent taxonomy (plan §6.2). Deterministically dispatchable."""

    LOOKUP_CASE = "LOOKUP_CASE"
    LOOKUP_PERSON = "LOOKUP_PERSON"
    LOOKUP_LOCATION = "LOOKUP_LOCATION"
    NETWORK_QUERY = "NETWORK_QUERY"
    TREND_QUERY = "TREND_QUERY"
    SEASONAL_QUERY = "SEASONAL_QUERY"
    HOTSPOT_QUERY = "HOTSPOT_QUERY"
    OFFENDER_PROFILE = "OFFENDER_PROFILE"
    SIMILAR_CASE = "SIMILAR_CASE"
    INVESTIGATION_SUMMARY = "INVESTIGATION_SUMMARY"
    DEMOGRAPHIC_INSIGHT = "DEMOGRAPHIC_INSIGHT"
    FINANCIAL_LINK = "FINANCIAL_LINK"
    EARLY_WARNING = "EARLY_WARNING"
    GENERAL_QA = "GENERAL_QA"


class AgentName(StrEnum):
    """The approved five-agent architecture. No other agents exist.

    Memory, authorization, evidence, PDF, audit and language are deterministic
    *services*, not agents (see ADR-0002).
    """

    SUPERVISOR = "SupervisorAgent"
    DATA_RETRIEVAL = "DataRetrievalAgent"
    CRIME_ANALYTICS = "CrimeAnalyticsAgent"
    NETWORK_INTELLIGENCE = "NetworkIntelligenceAgent"
    INVESTIGATION_SUPPORT = "InvestigationSupportAgent"


class EdgeType(StrEnum):
    CO_ACCUSED = "CO_ACCUSED"
    SAME_LOCATION = "SAME_LOCATION"
    SAME_VICTIM = "SAME_VICTIM"
    ARRESTED_BY = "ARRESTED_BY"
    SAME_MODUS_OPERANDI = "SAME_MODUS_OPERANDI"
    REPEAT_OFFENDER = "REPEAT_OFFENDER"
    MONEY_FLOW = "MONEY_FLOW"
    SAME_AS = "SAME_AS"


class NodeType(StrEnum):
    PERSON = "Person"
    CASE = "Case"
    LOCATION = "Location"
    OFFICER = "Officer"
    POLICE_STATION = "PoliceStation"
    ENTITY = "Entity"


class BatchStatus(StrEnum):
    RECEIVED = "RECEIVED"
    LOADED = "LOADED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"


class DQSeverity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    BLOCKER = "BLOCKER"


class EvidenceKind(StrEnum):
    CASE = "case"
    PERSON = "person"
    AGGREGATE = "aggregate"
    EDGE = "edge"
    ALERT = "alert"
    TRANSACTION = "transaction"


class Provenance(StrEnum):
    """Whether a statement is read from source records or inferred by CIP.

    Every inferred relationship must be explicitly marked (project mandate).
    """

    SOURCE_RECORD = "source_record"
    DETERMINISTIC_COMPUTATION = "deterministic_computation"
    INFERRED = "inferred"
    SYNTHETIC_EXTENSION = "synthetic_extension"
