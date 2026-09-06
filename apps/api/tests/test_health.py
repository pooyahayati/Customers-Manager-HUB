from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from customers_manager_hub import health as health_module
from customers_manager_hub.config import Settings
from customers_manager_hub.main import create_app


def test_settings() -> Settings:
    return Settings(
        app_env="test",
        database_url="postgresql://user:password@db:5432/customers_manager_hub",
        redis_url="redis://cache:6379/0",
        _env_file=None,
    )


def test_health_returns_ok() -> None:
    with TestClient(create_app(test_settings())) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_ok_when_dependencies_are_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_module, "check_postgres", AsyncMock(return_value=True))
    monkeypatch.setattr(health_module, "check_redis", AsyncMock(return_value=True))

    with TestClient(create_app(test_settings())) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "dependencies": {"postgres": "ok", "redis": "ok"},
    }


def test_ready_returns_503_when_a_dependency_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_module, "check_postgres", AsyncMock(return_value=False))
    monkeypatch.setattr(health_module, "check_redis", AsyncMock(return_value=True))

    with TestClient(create_app(test_settings())) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {"postgres": "unavailable", "redis": "ok"},
    }
