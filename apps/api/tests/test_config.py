import pytest
from pydantic import ValidationError

from customers_manager_hub.config import Settings


def test_settings_parse_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_DEBUG", "true")
    monkeypatch.setenv("APP_LOG_LEVEL", "debug")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://user:password@db:5432/customers_manager_hub",
    )
    monkeypatch.setenv("REDIS_URL", "redis://cache:6379/2")

    settings = Settings(_env_file=None)

    assert settings.app_env == "test"
    assert settings.app_debug is True
    assert settings.app_log_level == "DEBUG"
    assert settings.postgres_dsn.startswith("postgresql://")
    assert settings.redis_url == "redis://cache:6379/2"


def test_production_rejects_bootstrap_database_credentials() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            database_url=(
                "postgresql://cmh:change-me@postgres:5432/customers_manager_hub"
            ),
            _env_file=None,
        )
