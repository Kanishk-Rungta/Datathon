"""Composition root.

Every dependency is constructed here, once, and wired by hand. There is no
service locator and no runtime magic: if a component is missing a dependency,
this module fails at startup rather than mid-conversation.

Swapping SQLite for the Catalyst Data Store is a change to :meth:`_build_store`
and nothing else — that is the whole point of the port/adapter split.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any

from ..application.agents import (
    CrimeAnalyticsAgent,
    DataRetrievalAgent,
    InvestigationSupportAgent,
    NetworkIntelligenceAgent,
    SupervisorAgent,
)
from ..application.analytics import AnalyticsEngine
from ..application.graph import EntityResolver, FinancialAnalyzer, GraphBuilder, GraphService
from ..application.nlu import NLUEngine
from ..application.pipeline import DataQualitySuite, IntelligenceRefresher, SeedPipeline
from ..application.rag import RetrievalService
from ..application.services import (
    AnswerComposer,
    AuditService,
    AuthorizationService,
    ConversationLanguageService,
    IdentityService,
    MemoryService,
    PDFExportService,
)
from ..config import Settings, get_settings
from ..domain.ports import DataStore, FileStore
from ..infrastructure.db.kv_store import RelationalKeyValueStore
from ..infrastructure.db.migrations import apply_migrations
from ..infrastructure.db.repositories import (
    AlertRepository,
    AnalyticsRepository,
    AuditRepository,
    CaseRepository,
    ControlRepository,
    ConversationRepository,
    EmbeddingRepository,
    EventCalendarRepository,
    FinancialRepository,
    GraphRepository,
    HotspotRepository,
    IdentityRepository,
    PriorityRepository,
    ReferenceRepository,
    UserRepository,
)
from ..infrastructure.db.sqlite_store import SQLiteDataStore
from ..infrastructure.embeddings import HashedNgramEmbeddingModel
from ..infrastructure.filestore.local import LocalFileStore
from ..infrastructure.language import build_language_service
from ..infrastructure.llm.gateway import LLMGatewayImpl
from ..infrastructure.observability import SystemClock, configure_logging, get_logger

LOGGER = get_logger(__name__)


@dataclass
class Container:
    """Holds every constructed component for the lifetime of the process."""

    settings: Settings
    store: DataStore
    filestore: FileStore
    clock: Any

    # repositories
    reference: ReferenceRepository
    cases: CaseRepository
    analytics: AnalyticsRepository
    graph_repository: GraphRepository
    embeddings: EmbeddingRepository
    identities: IdentityRepository
    hotspots: HotspotRepository
    alerts: AlertRepository
    priority: PriorityRepository
    financial: FinancialRepository
    users: UserRepository
    conversations: ConversationRepository
    audit_repository: AuditRepository
    control: ControlRepository
    events: EventCalendarRepository

    # services
    audit: AuditService
    authorization: AuthorizationService
    identity_service: IdentityService
    #: Verifies bearer tokens. The local ``IdentityService`` locally; the
    #: Catalyst provider when ``KSPCIP_IDENTITY_BACKEND=catalyst``.
    identity_provider: Any
    cache: Any
    memory: MemoryService
    language: ConversationLanguageService
    composer: AnswerComposer
    pdf: PDFExportService

    # application
    llm: Any
    engine: AnalyticsEngine
    graph: GraphService
    retrieval: RetrievalService
    nlu: NLUEngine
    supervisor: SupervisorAgent
    data_retrieval: DataRetrievalAgent
    crime_analytics: CrimeAnalyticsAgent
    network_intelligence: NetworkIntelligenceAgent
    investigation_support: InvestigationSupportAgent
    refresher: IntelligenceRefresher
    seeder: SeedPipeline
    dq: DataQualitySuite

    def warm(self) -> dict[str, Any]:
        """Load caches that would otherwise be built on the first request."""
        counts = self.reference.warm()
        documents = self.retrieval.warm()
        labels = {
            f"person:{identity['identity_id']}": identity["canonical_name"]
            for identity in self.identities.identities()
        }
        if labels:
            self.graph.set_labels(labels)
        return {"reference": counts, "embedding_documents": documents, "graph_labels": len(labels)}

    def health(self) -> dict[str, Any]:
        try:
            case_count = self.control.store_row_count("curated_CaseMaster")
        except Exception:  # noqa: BLE001 - health must not raise
            case_count = -1
        return {
            "status": "ok" if case_count >= 0 else "degraded",
            "environment": str(self.settings.environment),
            "datastore": str(self.settings.datastore_backend),
            "filestore": str(self.settings.filestore_backend),
            "keyvalue": str(self.settings.keyvalue_backend),
            "cache": str(self.settings.cache_backend),
            "identity": str(self.settings.identity_backend),
            "llm_provider": str(self.settings.llm_provider),
            "language_provider": self.language.provider_name,
            "language_full_fidelity": self.language.is_full_fidelity,
            "embedding_model": self.settings.embedding_model_name,
            "cases": case_count,
            "seeded": case_count > 0,
        }


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    problems = settings.deployment_problems()
    if problems:
        # Fail at startup with every problem at once, rather than surfacing
        # them one restart at a time or mid-conversation.
        raise ValueError("Configuration is not deployable:\n  - " + "\n  - ".join(problems))

    clock = SystemClock()
    store = _build_store(settings)
    apply_migrations(store)
    filestore = _build_filestore(settings)

    reference = ReferenceRepository(store)
    cases = CaseRepository(store)
    analytics = AnalyticsRepository(store)
    graph_repository = GraphRepository(store)
    embeddings = EmbeddingRepository(store)
    identities = IdentityRepository(store)
    hotspots = HotspotRepository(store)
    alerts = AlertRepository(store)
    priority = PriorityRepository(store)
    financial = FinancialRepository(store)
    users = UserRepository(store)
    conversations = ConversationRepository(store, ttl_days=settings.conversation_memory_ttl_days)
    audit_repository = AuditRepository(store)
    control = ControlRepository(store)
    events = EventCalendarRepository(store)
    kv = _build_kv(settings, store)
    cache = _build_cache(settings)

    audit = AuditService(audit_repository)
    authorization = AuthorizationService(reference)
    identity_service = IdentityService(users, authorization, settings)
    identity_provider = _build_identity(settings, users, authorization, identity_service)
    memory = MemoryService(conversations, kv, window_turns=settings.conversation_window_turns,
                           ttl_days=settings.conversation_memory_ttl_days)
    language_provider = build_language_service(settings)
    language = ConversationLanguageService(language_provider)
    pdf = PDFExportService(filestore)

    llm = LLMGatewayImpl(settings)
    composer = AnswerComposer(llm, enable_polish=not llm.is_local)

    engine = AnalyticsEngine(
        analytics,
        reference,
        hotspot_grid_metres=settings.hotspot_grid_metres,
        hotspot_min_cases=settings.hotspot_min_cases,
        early_warning_sigma=settings.early_warning_sigma,
        early_warning_min_baseline=settings.early_warning_min_baseline,
    )
    graph = GraphService(graph_repository)
    embedding_model = HashedNgramEmbeddingModel(
        dimensions=settings.embedding_dimensions, model_name=settings.embedding_model_name
    )
    retrieval = RetrievalService(
        cases, embeddings, embedding_model,
        top_k=settings.rag_top_k, min_similarity=settings.rag_min_similarity,
    )
    nlu = NLUEngine(reference, llm)

    data_retrieval = DataRetrievalAgent(
        audit, cases, reference, retrieval, authorization,
        default_page_size=settings.default_case_page_size,
    )
    crime_analytics = CrimeAnalyticsAgent(audit, engine, analytics, reference, hotspots, alerts, authorization)
    analyzer = FinancialAnalyzer()
    network_intelligence = NetworkIntelligenceAgent(
        audit, graph, identities, cases, financial, analyzer, authorization
    )
    investigation_support = InvestigationSupportAgent(
        audit, cases, engine, priority, retrieval, graph, identities, authorization
    )
    supervisor = SupervisorAgent(
        nlu=nlu, memory=memory, language=language, composer=composer,
        authorization=authorization, audit=audit,
        data_retrieval=data_retrieval, crime_analytics=crime_analytics,
        network_intelligence=network_intelligence, investigation_support=investigation_support,
        clock=clock,
    )

    resolver = EntityResolver(
        tau_high=settings.entity_resolution_tau_high,
        tau_low=settings.entity_resolution_tau_low,
    )
    builder = GraphBuilder()
    refresher = IntelligenceRefresher(
        cases=cases, reference=reference, analytics=analytics,
        graph_repository=graph_repository, graph_service=graph, identities=identities,
        hotspots=hotspots, alerts=alerts, priority=priority, control=control,
        retrieval=retrieval, engine=engine, resolver=resolver, builder=builder, clock=clock,
    )
    dq = DataQualitySuite(store, control)
    seeder = SeedPipeline(
        store=store, filestore=filestore, control=control, users=users,
        identity_service=identity_service, dq=dq, refresher=refresher,
        reference=reference, clock=clock, seed=settings.synthetic_seed,
    )

    LOGGER.info("container_built", extra={
        "environment": str(settings.environment),
        "llm_provider": str(settings.llm_provider),
        "language_provider": language.provider_name,
    })

    return Container(
        settings=settings, store=store, filestore=filestore, clock=clock,
        reference=reference, cases=cases, analytics=analytics,
        graph_repository=graph_repository, embeddings=embeddings, identities=identities,
        hotspots=hotspots, alerts=alerts, priority=priority, financial=financial,
        users=users, conversations=conversations, audit_repository=audit_repository,
        control=control, events=events, audit=audit, authorization=authorization,
        identity_service=identity_service, identity_provider=identity_provider,
        cache=cache, memory=memory, language=language,
        composer=composer, pdf=pdf, llm=llm, engine=engine, graph=graph,
        retrieval=retrieval, nlu=nlu, supervisor=supervisor,
        data_retrieval=data_retrieval, crime_analytics=crime_analytics,
        network_intelligence=network_intelligence, investigation_support=investigation_support,
        refresher=refresher,
        seeder=seeder, dq=dq,
    )


def _build_store(settings: Settings) -> DataStore:
    from ..config.settings import DataStoreBackend

    if settings.datastore_backend is DataStoreBackend.CATALYST:
        from ..infrastructure.catalyst.datastore import CatalystDataStore

        return CatalystDataStore(settings)
    settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteDataStore(settings.sqlite_path, timeout=settings.sqlite_timeout_seconds)


def _build_filestore(settings: Settings) -> FileStore:
    """Bind the file store.

    Kept separate from :func:`_build_store` on purpose: an export written to a
    Catalyst function's local disk disappears at the next cold start, and the
    audit row would then cite a file nobody can fetch. The settings validator
    refuses that combination outright.
    """
    from ..config.settings import FileStoreBackend

    if settings.filestore_backend is FileStoreBackend.CATALYST:
        from ..infrastructure.catalyst.stratus import StratusFileStore

        return StratusFileStore(settings)
    settings.filestore_root.mkdir(parents=True, exist_ok=True)
    return LocalFileStore(settings.filestore_root)


def _build_kv(settings: Settings, store: DataStore) -> Any:
    from ..config.settings import KeyValueBackend

    if settings.keyvalue_backend is KeyValueBackend.CATALYST:
        from ..infrastructure.catalyst.nosql import CatalystKeyValueStore

        return CatalystKeyValueStore(store, table=settings.catalyst_nosql_table)
    return RelationalKeyValueStore(store)


def _build_cache(settings: Settings) -> Any:
    from ..config.settings import CacheBackend

    if settings.cache_backend is CacheBackend.CATALYST:
        from ..infrastructure.catalyst.cache import CatalystCache

        return CatalystCache(settings)
    from ..infrastructure.catalyst.cache import InProcessCache

    return InProcessCache()


def _build_identity(settings: Settings, users: UserRepository, authorization: AuthorizationService,
                    local: IdentityService) -> Any:
    """Return the component that turns a bearer token into a ``Principal``.

    The local service also owns password login, so it is always constructed;
    the Catalyst provider only replaces token verification.
    """
    from ..config.settings import IdentityBackend

    if settings.identity_backend is IdentityBackend.CATALYST:
        from ..infrastructure.catalyst.identity import CatalystIdentityProvider

        return CatalystIdentityProvider(users, authorization, settings)
    return local


@functools.lru_cache(maxsize=1)
def get_container() -> Container:
    return build_container()


def reset_container() -> None:
    """Used by tests to force a rebuild against a fresh temporary database."""
    get_container.cache_clear()
