"""Central configuration.

Every runtime knob of the platform is declared here and nowhere else. Modules
never read ``os.environ`` directly; they receive a :class:`Settings` instance
through the dependency-injection container (``ksp_cip.container``).

Configuration precedence: process environment > ``.env`` file > defaults.
The defaults are chosen so that ``uvicorn ksp_cip.main:app`` starts a fully
functional platform with **zero credentials**: the local deterministic LLM
provider, the local deterministic language provider, a SQLite data store and a
local file store. Credentials only switch on higher-fidelity providers.
"""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Environment(StrEnum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class DataStoreBackend(StrEnum):
    """Which implementation of the :class:`DataStore` port is bound."""

    SQLITE = "sqlite"
    CATALYST = "catalyst"


class LLMProviderName(StrEnum):
    """Reasoning/paraphrase providers behind the LLM Gateway.

    ``LOCAL`` is a deterministic, offline, template-driven provider. It is the
    default because the platform must never *depend* on an LLM for factual
    output (see ADR-0003); the LLM only smooths language.
    """

    LOCAL = "local"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    GROQ = "groq"
    OPENAI_COMPATIBLE = "openai_compatible"


class LanguageProviderName(StrEnum):
    LOCAL = "local"
    BHASHINI = "bhashini"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KSPCIP_",
        env_file=os.environ.get("KSPCIP_ENV_FILE", str(REPO_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------------------------------------------------------------- general
    app_name: str = "KSP Crime Intelligence Platform"
    environment: Environment = Environment.LOCAL
    debug: bool = False
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])

    # ------------------------------------------------------------------ store
    datastore_backend: DataStoreBackend = DataStoreBackend.SQLITE
    sqlite_path: Path = Field(default=BACKEND_ROOT / "var" / "ksp_cip.db")
    filestore_root: Path = Field(default=BACKEND_ROOT / "var" / "filestore")
    sqlite_timeout_seconds: float = 30.0

    # --------------------------------------------------------------- catalyst
    catalyst_project_id: str | None = None
    catalyst_environment: str = "Development"
    catalyst_base_url: str = "https://api.catalyst.zoho.in"
    catalyst_oauth_refresh_token: str | None = None
    catalyst_oauth_client_id: str | None = None
    catalyst_oauth_client_secret: str | None = None
    catalyst_accounts_url: str = "https://accounts.zoho.in"
    catalyst_stratus_bucket: str = "cip-ingest"

    # -------------------------------------------------------------------- llm
    llm_provider: LLMProviderName = LLMProviderName.LOCAL
    llm_model: str = "local-deterministic-v1"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_timeout_seconds: float = 30.0
    llm_max_output_tokens: int = 900
    llm_temperature: float = 0.1
    llm_daily_token_budget: int = 2_000_000

    # --------------------------------------------------------------- language
    language_provider: LanguageProviderName = LanguageProviderName.LOCAL
    bhashini_base_url: str = "https://dhruva-api.bhashini.gov.in/services/inference/pipeline"
    bhashini_config_url: str = "https://meity-auth.ulcacontrib.org/ulca/apis/v0/model/getModelsPipeline"
    bhashini_user_id: str | None = None
    bhashini_api_key: str | None = None
    bhashini_pipeline_id: str = "64392f96daac500b55c543cd"
    bhashini_timeout_seconds: float = 45.0

    # ------------------------------------------------------------- embeddings
    embedding_model_name: str = "hashed-char-ngram-tfidf-v1"
    embedding_dimensions: int = 512

    # ------------------------------------------------------------------ auth
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 12 * 60 * 60
    password_hash_iterations: int = 120_000

    # ------------------------------------------------------------- behaviour
    synthetic_seed: int = 20260725
    default_case_page_size: int = 25
    max_case_page_size: int = 200
    conversation_window_turns: int = 8
    conversation_memory_ttl_days: int = 30
    rag_top_k: int = 8
    rag_min_similarity: float = 0.18
    hotspot_grid_metres: int = 750
    hotspot_min_cases: int = 5
    early_warning_sigma: float = 2.0
    early_warning_min_baseline: float = 1.0
    entity_resolution_tau_high: float = 0.90
    entity_resolution_tau_low: float = 0.72

    # ----------------------------------------------------------- observability
    log_level: str = "INFO"
    log_json: bool = True
    audit_retention_days: int = 2557  # ~7 years, architecture §12.3

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("sqlite_path", "filestore_root", mode="before")
    @classmethod
    def _expand_paths(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value).expanduser()
        return value

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    def ensure_directories(self) -> None:
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.filestore_root.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings


def reset_settings_cache() -> None:
    """Used by tests that mutate the environment."""
    get_settings.cache_clear()
