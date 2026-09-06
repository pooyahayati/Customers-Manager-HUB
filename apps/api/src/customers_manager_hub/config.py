from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnvironment = Literal["development", "test", "staging", "production"]


class Settings(BaseSettings):
    """Typed runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: AppEnvironment = "development"
    app_name: str = "Customers Manager HUB"
    app_debug: bool = False
    app_log_level: str = "INFO"

    database_url: str = "postgresql://cmh:change-me@localhost:5432/customers_manager_hub"
    redis_url: str = "redis://localhost:6379/0"
    dependency_timeout_seconds: int = Field(default=2, ge=1, le=30)

    openai_api_key: SecretStr | None = None
    google_gemini_api_key: SecretStr | None = None

    @field_validator("app_log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if normalized not in allowed:
            raise ValueError(f"APP_LOG_LEVEL must be one of {sorted(allowed)}")
        return normalized

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("DATABASE_URL must use PostgreSQL")
        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: str) -> str:
        if not value.startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must use redis:// or rediss://")
        return value

    @model_validator(mode="after")
    def reject_insecure_production_defaults(self) -> Self:
        if self.app_env == "production" and "change-me" in self.database_url:
            raise ValueError("Production DATABASE_URL must not use bootstrap credentials")
        return self

    @property
    def postgres_dsn(self) -> str:
        """Return a Psycopg-compatible PostgreSQL DSN."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    @property
    def sqlalchemy_database_url(self) -> str:
        """Return a SQLAlchemy URL using the existing Psycopg driver."""
        if self.database_url.startswith("postgresql+psycopg://"):
            return self.database_url
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
