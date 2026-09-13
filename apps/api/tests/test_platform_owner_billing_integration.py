import asyncio
import base64
import os
import selectors
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from customers_manager_hub.billing_models import BusinessWallet, PlatformAIModelPrice
from customers_manager_hub.billing_runtime import calculate_charge_rial
from customers_manager_hub.config import Settings
from customers_manager_hub.main import create_app
from customers_manager_hub.models import PlatformUser, Tenant, TenantMembership, TenantRole
from customers_manager_hub.platform_ai_models import PlatformAIProviderCredential
from customers_manager_hub.security import hash_password

RUN_DB_INTEGRATION = os.environ.get("RUN_DB_INTEGRATION") == "1"
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://cmh:change-me@localhost:5432/customers_manager_hub",
)
TEST_ENCRYPTION_KEY = base64.urlsafe_b64encode(b"x" * 32).decode()
TEST_SETTINGS = Settings(
    app_env="test",
    database_url=DATABASE_URL,
    encryption_key=SecretStr(TEST_ENCRYPTION_KEY),
)
SYNC_ENGINE = create_engine(TEST_SETTINGS.sqlalchemy_database_url)

pytestmark = pytest.mark.skipif(
    not RUN_DB_INTEGRATION,
    reason="PostgreSQL integration tests require RUN_DB_INTEGRATION=1",
)


@pytest.fixture(autouse=True)
def clean_platform_tables() -> Iterator[None]:
    truncate_database()
    yield
    truncate_database()


def truncate_database() -> None:
    with SYNC_ENGINE.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE business_wallet_transactions, business_wallets, "
                "platform_ai_model_pricing, platform_billing_settings, "
                "platform_ai_task_routes, platform_ai_task_profiles, "
                "platform_ai_provider_credentials, ai_execution_traces, "
                "audit_events, auth_sessions, tenant_memberships, "
                "platform_users, tenants CASCADE"
            )
        )
        connection.execute(
            text("INSERT INTO platform_billing_settings (id, display_unit) VALUES (1, 'rial')")
        )


def login(client: TestClient, email: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200


def make_test_client() -> TestClient:
    return TestClient(
        create_app(TEST_SETTINGS),
        backend_options={
            "loop_factory": lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        },
    )


def test_non_owner_is_denied_by_every_platform_admin_endpoint() -> None:
    with Session(SYNC_ENGINE) as db:
        business = Tenant(name="Denied Business", slug="denied-business")
        user = PlatformUser(
            email="denied-admin@example.com",
            password_hash=hash_password("denied password 1234"),
        )
        db.add_all([business, user])
        db.flush()
        db.add(
            TenantMembership(
                tenant_id=business.id,
                user_id=user.id,
                role=TenantRole.ADMIN.value,
            )
        )
        db.commit()

    business_id = "00000000-0000-0000-0000-000000000010"
    user_id = "00000000-0000-0000-0000-000000000011"
    cases: list[tuple[str, str, dict[str, object] | None]] = [
        ("GET", "/api/v1/platform/businesses", None),
        ("POST", "/api/v1/platform/businesses", {"name": "Blocked", "slug": "blocked"}),
        ("PATCH", f"/api/v1/platform/businesses/{business_id}", {"is_active": False}),
        ("GET", f"/api/v1/platform/businesses/{business_id}/users", None),
        (
            "POST",
            f"/api/v1/platform/businesses/{business_id}/users",
            {
                "email": "blocked@example.com",
                "password": "blocked password 1234",
                "role": "agent",
            },
        ),
        (
            "PATCH",
            f"/api/v1/platform/businesses/{business_id}/users/{user_id}",
            {"is_active": False},
        ),
        ("GET", "/api/v1/platform/ai/providers", None),
        ("PUT", "/api/v1/platform/ai/providers/openai", {"api_key": "blocked-key"}),
        ("DELETE", "/api/v1/platform/ai/providers/openai", None),
        ("POST", "/api/v1/platform/ai/providers/openai/test", None),
        ("GET", "/api/v1/platform/ai/providers/openai/models", None),
        ("GET", "/api/v1/platform/ai/task-profiles", None),
        (
            "PUT",
            "/api/v1/platform/ai/task-profiles/customer_response",
            {
                "routes": [
                    {
                        "provider": "openai",
                        "model_id": "blocked-model",
                        "parameters": {},
                    }
                ]
            },
        ),
        ("GET", "/api/v1/platform/billing/settings", None),
        ("PUT", "/api/v1/platform/billing/settings", {"display_unit": "rial"}),
        ("GET", "/api/v1/platform/billing/pricing", None),
        (
            "PUT",
            "/api/v1/platform/billing/pricing",
            {
                "provider": "openai",
                "model_id": "blocked-model",
                "input_per_million_rial": 1,
                "output_per_million_rial": 1,
                "audio_per_minute_rial": 1,
            },
        ),
        ("GET", f"/api/v1/platform/billing/businesses/{business_id}/wallet", None),
        (
            "POST",
            f"/api/v1/platform/billing/businesses/{business_id}/wallet/credit",
            {"amount_rial": 1},
        ),
        (
            "GET",
            f"/api/v1/platform/billing/businesses/{business_id}/wallet/transactions",
            None,
        ),
    ]

    with make_test_client() as client:
        login(client, "denied-admin@example.com", "denied password 1234")
        for method, path, payload in cases:
            response = client.request(method, path, json=payload)
            assert response.status_code == 403, (method, path, response.text)


def test_charge_is_rounded_up_and_kept_in_integer_rial() -> None:
    price = PlatformAIModelPrice(
        provider="openai",
        model_id="priced-model",
        input_per_million_rial=500_000,
        output_per_million_rial=500_000,
        audio_per_minute_rial=60,
        effective_from=datetime.now(UTC),
    )

    assert (
        calculate_charge_rial(
            price,
            input_tokens=1,
            output_tokens=1,
            audio_seconds=30,
        )
        == 31
    )


def test_owner_business_users_global_ai_and_rial_wallet_flow() -> None:
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        initial_tenant = Tenant(name="Initial Business", slug="initial-business")
        owner = PlatformUser(
            email="owner@example.com",
            password_hash=hash_password("owner password 1234"),
            is_platform_owner=True,
        )
        db.add_all([initial_tenant, owner])
        db.flush()
        initial_tenant_id = initial_tenant.id
        owner_id = owner.id
        db.add_all(
            [
                TenantMembership(
                    tenant_id=initial_tenant_id,
                    user_id=owner_id,
                    role=TenantRole.OWNER.value,
                ),
                BusinessWallet(tenant_id=initial_tenant_id),
            ]
        )
        admin = PlatformUser(
            email="admin@example.com",
            password_hash=hash_password("admin password 1234"),
        )
        db.add(admin)
        db.flush()
        db.add(
            TenantMembership(
                tenant_id=initial_tenant_id,
                user_id=admin.id,
                role=TenantRole.ADMIN.value,
            )
        )
        db.commit()

    with make_test_client() as admin_client:
        login(admin_client, "admin@example.com", "admin password 1234")
        assert admin_client.get("/api/v1/platform/businesses").status_code == 403

    with make_test_client() as owner_client:
        login(owner_client, "owner@example.com", "owner password 1234")
        me = owner_client.get("/api/v1/auth/me")
        assert me.json()["is_platform_owner"] is True

        business_response = owner_client.post(
            "/api/v1/platform/businesses",
            json={"name": "Sales Team", "slug": "sales-team"},
        )
        assert business_response.status_code == 201
        business_id = business_response.json()["id"]

        user_response = owner_client.post(
            f"/api/v1/platform/businesses/{business_id}/users",
            json={
                "email": "agent@example.com",
                "password": "agent password 1234",
                "role": "agent",
            },
        )
        assert user_response.status_code == 201
        user_id = user_response.json()["id"]

        display_response = owner_client.put(
            "/api/v1/platform/billing/settings",
            json={"display_unit": "toman"},
        )
        assert display_response.json() == {"display_unit": "toman"}

        credit_response = owner_client.post(
            f"/api/v1/platform/billing/businesses/{business_id}/wallet/credit",
            json={"amount_rial": 20_000_000, "note": "manual test credit"},
        )
        assert credit_response.status_code == 201
        assert credit_response.json()["amount_rial"] == 20_000_000
        wallet_response = owner_client.get(
            f"/api/v1/platform/billing/businesses/{business_id}/wallet"
        )
        assert wallet_response.json()["balance_rial"] == 20_000_000

        credential_response = owner_client.put(
            "/api/v1/platform/ai/providers/openai",
            json={"api_key": "sk-test-secret-value"},
        )
        assert credential_response.status_code == 200
        assert credential_response.json()["source"] == "database"

        profile_response = owner_client.put(
            "/api/v1/platform/ai/task-profiles/customer_response",
            json={
                "routes": [
                    {
                        "provider": "openai",
                        "model_id": "gpt-test-model",
                        "parameters": {},
                    }
                ]
            },
        )
        assert profile_response.status_code == 200

        price_response = owner_client.put(
            "/api/v1/platform/billing/pricing",
            json={
                "provider": "openai",
                "model_id": "gpt-test-model",
                "input_per_million_rial": 1_000_000,
                "output_per_million_rial": 2_000_000,
                "audio_per_minute_rial": 0,
            },
        )
        assert price_response.status_code == 200

        with Session(SYNC_ENGINE) as db:
            credential = db.get(PlatformAIProviderCredential, "openai")
            owner = db.get(PlatformUser, owner_id)
            assert credential is not None
            assert b"sk-test-secret-value" not in credential.ciphertext
            assert owner is not None and owner.is_platform_owner

        with make_test_client() as agent_client:
            login(agent_client, "agent@example.com", "agent password 1234")
            summary = agent_client.get(f"/api/v1/tenants/{business_id}/billing/summary")
            assert summary.status_code == 200
            assert summary.json() == {
                "balance_rial": 20_000_000,
                "display_unit": "toman",
            }

        disabled = owner_client.patch(
            f"/api/v1/platform/businesses/{business_id}/users/{user_id}",
            json={"is_active": False},
        )
        assert disabled.status_code == 200
        assert disabled.json()["is_active"] is False

    with make_test_client() as disabled_client:
        response = disabled_client.post(
            "/api/v1/auth/login",
            json={"email": "agent@example.com", "password": "agent password 1234"},
        )
        assert response.status_code == 401
