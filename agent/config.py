"""Application configuration.

Every tunable value in Anchor arrives through environment variables (optionally
seeded from a local ``.env`` file). Nothing here reaches out to a network, so
importing this module stays cheap and side-effect free.
"""

from __future__ import annotations

import functools
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Service -----------------------------------------------------------
    APP_NAME: str = "anchor"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: Literal["dev", "test", "prod"] = "dev"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True

    # --- Authentication ----------------------------------------------------
    # Deliberately has no default: an unset secret must fail loudly at import
    # time rather than silently signing tokens with a well-known value.
    JWT_SECRET: str = Field(..., min_length=16)
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_MINUTES: int = 60
    JWT_ISSUER: str = "anchor"

    # --- Passwords ---------------------------------------------------------
    # Argon2id. The parameters are the RFC 9106 second-recommended profile,
    # scaled to whatever this host can afford; ANCHOR sets them in the
    # environment and the encoded hash records which were used, so raising
    # them later does not invalidate existing credentials.
    PASSWORD_MIN_LENGTH: int = 12
    PASSWORD_MAX_LENGTH: int = 200
    ARGON2_TIME_COST: int = 3
    ARGON2_MEMORY_COST_KIB: int = 65536
    ARGON2_PARALLELISM: int = 2
    #: Server-side pepper, mixed into the hash alongside the password. Rotating
    #: it invalidates every stored credential, so it is a real secret and is
    #: blanked out of the image.
    CREDENTIAL_PEPPER: str = ""
    #: Open registration. A deployment that provisions users by invitation
    #: turns this off and creates accounts through the invite flow instead.
    AUTH_ALLOW_REGISTRATION: bool = True
    #: The unauthenticated ``POST /auth/token`` endpoint. It is a development
    # affordance - no password, self-selected role - and the application
    #: refuses to serve it when ENVIRONMENT is prod, whatever this says.
    AUTH_ALLOW_DEMO_TOKENS: bool = True
    REFRESH_TOKEN_EXPIRY_DAYS: int = 30
    PASSWORD_RESET_EXPIRY_MINUTES: int = 60
    #: First user created on an empty database, so a fresh deployment has a
    #: way in. Applied once, then ignored.
    BOOTSTRAP_ADMIN_EMAIL: str = ""
    BOOTSTRAP_ADMIN_PASSWORD: str = ""

    # --- Database ----------------------------------------------------------
    # Empty means "no database configured", which the persistence-backed
    # endpoints report as 503 rather than failing in some less obvious way.
    DATABASE_URL: str = ""
    #: Applied by ``python -m agent.db.seed``. Creates the bootstrap admin and,
    # optionally, a demo workspace.
    SEED_DEMO_WORKSPACE: bool = False

    # --- Object storage ----------------------------------------------------
    # `local` writes under STORAGE_LOCAL_DIR and is the development default.
    # `s3` is required in production: a container filesystem is wiped on every
    # deploy, so uploaded documents stored there would not survive one.
    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    STORAGE_LOCAL_DIR: str = "data/uploads"
    S3_BUCKET: str = ""
    S3_REGION: str = "us-east-1"
    S3_ENDPOINT_URL: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    #: Path prefix inside the bucket, so several environments can share one.
    S3_KEY_PREFIX: str = "anchor"

    # --- Background worker -------------------------------------------------
    WORKER_POLL_SECONDS: float = 2.0
    WORKER_MAX_ATTEMPTS: int = 3

    # --- Rate limiting and quotas -----------------------------------------
    # Process-local. Behind more than one API instance this becomes a
    # per-instance ceiling rather than a global one; the limits below are
    # sized to be a useful backstop rather than a hard global guarantee.
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS: int = 120
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    RATE_LIMIT_QUERY_REQUESTS: int = 20
    RATE_LIMIT_INGEST_REQUESTS: int = 10
    #: Daily answered-question ceiling per workspace. 0 disables the check.
    QUOTA_QUERIES_PER_DAY: int = 0

    # --- Vector store ------------------------------------------------------
    CHROMA_PERSIST_DIR: str = "chroma_data"
    CHROMA_COLLECTION: str = "anchor_kb"
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    CHUNK_SIZE_TOKENS: int = 500
    CHUNK_OVERLAP_TOKENS: int = 50
    RETRIEVER_TOP_K: int = 4

    # --- Ingestion / OCR ---------------------------------------------------
    OCR_ENABLED: bool = True
    OCR_LANGUAGE: str = "eng"
    OCR_MIN_CHARS_PER_PAGE: int = 40
    # Absolute path to the tesseract binary, for platforms where it is not on
    # PATH (notably the Windows installer, which does not modify PATH).
    TESSERACT_CMD: str | None = None
    MAX_UPLOAD_MB: int = 25

    # --- Providers ---------------------------------------------------------
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_DEFAULT_MODEL: str = "llama3.2:3b"
    GROQ_API_KEY: str | None = None
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_DEFAULT_MODEL: str = "llama-3.3-70b-versatile"
    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_DEFAULT_MODEL: str = "gpt-4o-mini"
    GEMINI_API_KEY: str | None = None
    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"
    GEMINI_DEFAULT_MODEL: str = "gemini-1.5-flash"

    # --- Routing -----------------------------------------------------------
    ROUTER_DEFAULT_MODEL: str = "ollama/llama3.2:3b"
    ROUTER_FALLBACK_CHAIN: str = "ollama,groq"
    ROUTER_MAX_TOOL_ITERATIONS: int = 3

    # --- Model execution ---------------------------------------------------
    LLM_TIMEOUT_SECONDS: float = 60.0
    PROVIDER_MAX_RETRIES: int = 1
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_TOKENS: int = 1024

    # --- Cost model (USD per 1M tokens) ------------------------------------
    # Supplied as JSON objects, e.g. {"groq/llama-3.3-70b-versatile": 0.59}.
    # A deployment that does not price a model leaves it out, and the provider
    # reports 0.0 rather than guessing.
    #
    # ``NoDecode`` stops pydantic-settings from JSON-parsing these before
    # validation, so the blank-value case below is reachable instead of
    # raising a SettingsError while the environment is being read.
    COST_INPUT_PER_MTOK: Annotated[dict[str, float], NoDecode] = Field(default_factory=dict)
    COST_OUTPUT_PER_MTOK: Annotated[dict[str, float], NoDecode] = Field(default_factory=dict)

    # --- Guardrails --------------------------------------------------------
    QUERY_MAX_CHARS: int = 2000
    TICKETS_DIR: str = "data/tickets"

    # --- Browser access ----------------------------------------------------
    # Origins allowed to call the API directly from a page. The Next.js
    # frontend proxies through its own server, so production needs none of
    # these: set CORS_ALLOWED_ORIGINS to an empty string to disable CORS
    # entirely, which is the correct production posture.
    CORS_ALLOWED_ORIGINS: str = (
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000,http://127.0.0.1:8000"
    )

    @field_validator("LOG_LEVEL")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("COST_INPUT_PER_MTOK", "COST_OUTPUT_PER_MTOK", mode="before")
    @classmethod
    def _blank_cost_map_is_no_rates(cls, value: object) -> object:
        """Treat an unset cost map as "no rates" rather than a validation error.

        The natural way to write "this deployment prices nothing" in an env
        file is to leave the line blank, and a blank string is not a valid
        ``dict[str, float]``. Without this, `cp .env.example .env` would stop
        the service from starting — a failure that only ever shows up on a
        genuinely new clone, never on a working one.
        """
        if value is None:
            return {}
        if isinstance(value, str) and not value.strip():
            return {}
        return value

    @property
    def fallback_chain(self) -> list[str]:
        """Provider ids to try, in order, when the primary selection fails."""
        raw = self.ROUTER_FALLBACK_CHAIN.replace(" ", "")
        return [part for part in raw.split(",") if part]


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that the JWT secret and vector-store directory stay consistent for
    the lifetime of the process. Tests that need a different configuration call
    ``get_settings.cache_clear()`` after patching the environment.
    """
    return Settings()  # type: ignore[call-arg]
