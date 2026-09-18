"""
Central runtime configuration for KERBERUS.

One `Settings` object (pydantic-settings) replaces the scattered `os.getenv`
calls. Environment variable names are unchanged, so existing `.env` files and
Docker secrets keep working. Values ending in `_FILE` (Docker secrets) are
resolved through `src.utils.secrets.get_secret`.

Usage:
    from src.config import get_settings
    settings = get_settings()
    settings.postgres_dsn
"""
from functools import lru_cache
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .utils.secrets import get_secret


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Application ---
    app_env: str = Field("development", alias="APP_ENV")
    app_version: str = Field("0.3.0", alias="APP_VERSION")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    cors_origins: str = Field("http://localhost:3000,http://localhost:8501", alias="CORS_ORIGINS")

    # --- PostgreSQL ---
    postgres_host: str = Field("localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(5432, alias="POSTGRES_PORT")
    postgres_db: str = Field("kerberus", alias="POSTGRES_DB")
    postgres_user: str = Field("kerberus_user", alias="POSTGRES_USER")

    # --- Redis ---
    redis_host: str = Field("localhost", alias="REDIS_HOST")
    redis_port: int = Field(6379, alias="REDIS_PORT")
    redis_db: int = Field(0, alias="REDIS_DB")

    # --- Qdrant ---
    qdrant_host: str = Field("localhost", alias="QDRANT_HOST")
    qdrant_port: int = Field(6333, alias="QDRANT_PORT")
    qdrant_api_key: Optional[str] = Field(None, alias="QDRANT_API_KEY")
    qdrant_collection_codex: str = Field("codex", alias="QDRANT_COLLECTION_CODEX")
    qdrant_collection_library: str = Field("library", alias="QDRANT_COLLECTION_LIBRARY")

    # --- Document store (full texts for the LLM) ---
    document_store_enabled: bool = Field(True, alias="DOCUMENT_STORE_ENABLED")
    parsed_data_dir: Optional[str] = Field(None, alias="PARSED_DATA_DIR")

    # --- Rate limiting ---
    rate_limit_enabled: bool = Field(True, alias="RATE_LIMIT_ENABLED")
    rate_limit_hourly: int = Field(50, alias="RATE_LIMIT_HOURLY")
    rate_limit_daily: int = Field(300, alias="RATE_LIMIT_DAILY")

    # --- Feature flags ---
    enable_pii_scrubbing: bool = Field(True, alias="ENABLE_PII_SCRUBBING")
    allow_self_registration: bool = Field(True, alias="ALLOW_SELF_REGISTRATION")

    # --- Dossier ---
    dossier_storage_path: str = Field("./data/dossier", alias="DOSSIER_STORAGE_PATH")
    dossier_kdf_iterations: int = Field(600_000, alias="DOSSIER_KDF_ITERATIONS")

    # --- Models (local or remote service) ---
    model_service_url: Optional[str] = Field(None, alias="MODEL_SERVICE_URL")
    model_service_timeout: float = Field(120.0, alias="MODEL_SERVICE_TIMEOUT")
    embedder_device: Optional[str] = Field(None, alias="EMBEDDER_DEVICE")
    reranker_device: str = Field("cpu", alias="RERANKER_DEVICE")

    # --- Derived values ---
    @property
    def postgres_password(self) -> str:
        return get_secret("POSTGRES_PASSWORD", "") or ""

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_password(self) -> Optional[str]:
        return get_secret("REDIS_PASSWORD", "") or None

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance (call `get_settings.cache_clear()` in tests)."""
    return Settings()
