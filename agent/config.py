"""Application configuration.

Every tunable value in Anchor arrives through environment variables (optionally
seeded from a local ``.env`` file). Nothing here reaches out to a network, so
importing this module stays cheap and side-effect free.
"""

from __future__ import annotations

import functools
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    COST_INPUT_PER_MTOK: dict[str, float] = Field(default_factory=dict)
    COST_OUTPUT_PER_MTOK: dict[str, float] = Field(default_factory=dict)

    # --- Guardrails --------------------------------------------------------
    QUERY_MAX_CHARS: int = 2000
    TICKETS_DIR: str = "data/tickets"

    # --- Browser access ----------------------------------------------------
    # Origins allowed to call the API from a page served elsewhere. Only the
    # bundled demo page needs this: it is same-origin, so this exists purely so
    # a UI can be developed on a different port. Set to an empty string to
    # disable CORS entirely, which is what a real deployment should do.
    CORS_ALLOWED_ORIGINS: str = "http://localhost:8000,http://127.0.0.1:8000"

    @field_validator("LOG_LEVEL")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

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
