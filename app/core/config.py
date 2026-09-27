"""
Application configuration.

Follows 12-factor app principles: all configuration is read from environment
variables (with sane local defaults for developer convenience), never
hardcoded. A single `Settings` object is constructed once and cached via
`get_settings()` so the rest of the app can depend on it without re-parsing
the environment on every call.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App metadata ---
    app_name: str = "Money Transfer System"
    app_env: Literal["local", "test", "staging", "production"] = "local"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000

    # --- Database ---
    # asyncpg driver for async SQLAlchemy engine.
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/money_transfer"
    )
    # Separate sync URL for Alembic migrations (asyncpg can't be used by
    # Alembic's default sync runner without extra ceremony, so psycopg2/psycopg
    # is used for migrations only).
    database_url_sync: str = Field(
        default="postgresql+psycopg2://postgres:postgres@localhost:5432/money_transfer"
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # --- Business rules ---
    default_currency: str = "USD"
    max_transaction_history_page_size: int = 100
    default_transaction_history_page_size: int = 20

    # --- Logging ---
    log_level: str = "INFO"
    log_json: bool = True

    # --- Idempotency ---
    idempotency_key_ttl_hours: int = 24


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton for process lifetime)."""
    return Settings()
