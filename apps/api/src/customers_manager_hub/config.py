import base64
import binascii
import ipaddress
from functools import lru_cache
from typing import Literal, Self
from urllib.parse import urlsplit

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

    encryption_key: SecretStr | None = None
    telegram_webhook_base_url: str | None = None

    openai_api_key: SecretStr | None = None
    google_gemini_api_key: SecretStr | None = None

    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "cmh-knowledge"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_force_path_style: bool = True

    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = Field(default=60, ge=10, le=3600)
    rate_limit_login_requests: int = Field(default=10, ge=1, le=10_000)
    rate_limit_public_requests: int = Field(default=120, ge=1, le=100_000)
    rate_limit_webhook_requests: int = Field(default=180, ge=1, le=100_000)
    trusted_proxy_cidrs: str = ""
    worker_max_delivery_attempts: int = Field(default=5, ge=1, le=50)

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

    @field_validator("encryption_key")
    @classmethod
    def validate_encryption_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        encoded = value.get_secret_value().strip()
        if not encoded:
            return None
        if encoded == "change-me":
            raise ValueError("ENCRYPTION_KEY must be a generated 32-byte base64 key")
        try:
            decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("ENCRYPTION_KEY must be valid URL-safe base64") from exc
        if len(decoded) != 32:
            raise ValueError("ENCRYPTION_KEY must decode to exactly 32 bytes")
        return SecretStr(encoded)

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def validate_trusted_proxy_cidrs(cls, value: str) -> str:
        normalized: list[str] = []
        for item in value.split(","):
            candidate = item.strip()
            if not candidate:
                continue
            try:
                network = ipaddress.ip_network(candidate, strict=False)
            except ValueError as exc:
                raise ValueError("TRUSTED_PROXY_CIDRS contains an invalid CIDR") from exc
            rendered = str(network)
            if rendered not in normalized:
                normalized.append(rendered)
        return ",".join(normalized)

    @field_validator("telegram_webhook_base_url")
    @classmethod
    def validate_telegram_webhook_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        if not normalized:
            return None
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("TELEGRAM_WEBHOOK_BASE_URL must be an absolute HTTP(S) URL")
        if parsed.query or parsed.fragment:
            raise ValueError("TELEGRAM_WEBHOOK_BASE_URL must not include query or fragment")
        return normalized

    @field_validator("s3_endpoint_url")
    @classmethod
    def validate_s3_endpoint_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        if not normalized:
            return None
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("S3_ENDPOINT_URL must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("S3_ENDPOINT_URL must not contain user information")
        if parsed.query or parsed.fragment:
            raise ValueError("S3_ENDPOINT_URL must not include query or fragment")
        return normalized

    @field_validator("s3_bucket")
    @classmethod
    def validate_s3_bucket(cls, value: str) -> str:
        normalized = value.strip()
        if not 3 <= len(normalized) <= 63:
            raise ValueError("S3_BUCKET must contain 3 to 63 characters")
        if normalized[0] in {".", "-"} or normalized[-1] in {".", "-"}:
            raise ValueError("S3_BUCKET must start and end with a letter or digit")
        if any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for character in normalized
        ):
            raise ValueError("S3_BUCKET must use lowercase DNS-compatible characters")
        if ".." in normalized or ".-" in normalized or "-." in normalized:
            raise ValueError("S3_BUCKET contains an invalid label boundary")
        return normalized

    @model_validator(mode="after")
    def reject_insecure_production_defaults(self) -> Self:
        if self.app_env == "production" and "change-me" in self.database_url:
            raise ValueError("Production DATABASE_URL must not use bootstrap credentials")
        if self.app_env in {"staging", "production"} and self.app_debug:
            raise ValueError("Staging/production APP_DEBUG must be false")
        if self.app_env in {"staging", "production"} and self.encryption_key is None:
            raise ValueError("Staging/production ENCRYPTION_KEY is required")
        if self.app_env == "production" and not self.rate_limit_enabled:
            raise ValueError("Production RATE_LIMIT_ENABLED must be true")
        if (
            self.app_env in {"staging", "production"}
            and self.telegram_webhook_base_url is not None
            and not self.telegram_webhook_base_url.startswith("https://")
        ):
            raise ValueError("Staging/production Telegram webhook base URL must use HTTPS")
        if (self.s3_access_key_id is None) != (self.s3_secret_access_key is None):
            raise ValueError("S3 access key ID and secret access key must be configured together")
        if self.s3_endpoint_url is not None and self.s3_access_key_id is None:
            raise ValueError("Configured S3 endpoint requires explicit S3 credentials")
        if (
            self.app_env in {"staging", "production"}
            and self.s3_endpoint_url is not None
            and not self.s3_endpoint_url.startswith("https://")
        ):
            raise ValueError("Staging/production S3 endpoint must use HTTPS")
        if self.app_env in {"staging", "production"} and self.s3_access_key_id is not None:
            access_key = self.s3_access_key_id.get_secret_value().strip()
            secret_key = (
                self.s3_secret_access_key.get_secret_value().strip()
                if self.s3_secret_access_key is not None
                else ""
            )
            if access_key in {"cmh-dev-access", "change-me"} or secret_key in {
                "cmh-dev-secret",
                "change-me",
            }:
                raise ValueError("Staging/production S3 credentials must not use development defaults")
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
