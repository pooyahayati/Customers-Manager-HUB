import asyncio
import os
from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from customers_manager_hub.ai_gateway import (
    AIGateway,
    AIProviderRegistry,
    AIRoutingError,
    GenerationRequest,
    MockAIProviderAdapter,
)
from customers_manager_hub.ai_models import (
    AIExecutionTrace,
    AITaskProfile,
    AITaskRoute,
    AITaskType,
)
from customers_manager_hub.config import Settings
from customers_manager_hub.database import create_database
from customers_manager_hub.main import create_app
from customers_manager_hub.models import (
    AuditEvent,
    PlatformUser,
    Tenant,
    TenantMembership,
    TenantRole,
)
from customers_manager_hub.security import hash_password

RUN_DB_INTEGRATION = os.environ.get("RUN_DB_INTEGRATION") == "1"
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://cmh:change-me@localhost:5432/customers_manager_hub",
)
TEST_SETTINGS = Settings(app_env="test", database_url=DATABASE_URL)
SYNC_ENGINE = create_engine(TEST_SETTINGS.sqlalchemy_database_url)

pytestmark = pytest.mark.skipif(
    not RUN_DB_INTEGRATION,
    reason="PostgreSQL integration tests require RUN_DB_INTEGRATION=1",
)


@pytest.fixture(autouse=True)
def clean_database() -> Iterator[None]:
    truncate_database()
    yield
    truncate_database()


def truncate_database() -> None:
    with SYNC_ENGINE.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE ai_execution_traces, ai_task_routes, ai_task_profiles, "
                "message_attachments, messages, conversations, external_identities, contacts, "
                "audit_events, auth_sessions, tenant_memberships, platform_users, tenants CASCADE"
            )
        )


def seed_tenant_user(
    *,
    tenant_slug: str,
    tenant_name: str,
    email: str,
    password: str,
    role: TenantRole = TenantRole.OWNER,
    tenant_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        if tenant_id is None:
            tenant = Tenant(slug=tenant_slug, name=tenant_name)
            db.add(tenant)
            db.flush()
            resolved_tenant_id = tenant.id
        else:
            resolved_tenant_id = tenant_id

        user = PlatformUser(email=email, password_hash=hash_password(password))
        db.add(user)
        db.flush()
        db.add(
            TenantMembership(
                tenant_id=resolved_tenant_id,
                user_id=user.id,
                role=role.value,
            )
        )
        db.commit()
        return resolved_tenant_id, user.id


def login(client: TestClient, email: str, password: str) -> Response:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    return response


def test_task_profile_api_covers_required_tasks_rbac_and_tenant_isolation() -> None:
    tenant_a, _ = seed_tenant_user(
        tenant_slug="ai-a",
        tenant_name="AI Tenant A",
        email="owner-a@example.com",
        password="owner password tenant a",
    )
    tenant_b, _ = seed_tenant_user(
        tenant_slug="ai-b",
        tenant_name="AI Tenant B",
        email="owner-b@example.com",
        password="owner password tenant b",
    )
    seed_tenant_user(
        tenant_slug="unused-admin",
        tenant_name="unused",
        email="admin-a@example.com",
        password="admin password tenant a",
        role=TenantRole.ADMIN,
        tenant_id=tenant_a,
    )
    seed_tenant_user(
        tenant_slug="unused-viewer",
        tenant_name="unused",
        email="viewer-a@example.com",
        password="viewer password tenant a",
        role=TenantRole.VIEWER,
        tenant_id=tenant_a,
    )

    with TestClient(create_app(TEST_SETTINGS)) as owner_client:
        login(owner_client, "owner-a@example.com", "owner password tenant a")
        for task_type in AITaskType:
            provider = "gemini" if task_type == AITaskType.VOICE_TRANSCRIPTION else "openai"
            response = owner_client.put(
                f"/api/v1/tenants/{tenant_a}/ai/task-profiles/{task_type.value}",
                json={
                    "timeout_seconds": 20,
                    "attempts_per_route": 2,
                    "routes": [
                        {
                            "provider": provider,
                            "model_id": f"model-{task_type.value}",
                            "parameters": {"temperature": 0},
                        }
                    ],
                },
            )
            assert response.status_code == 200
            assert response.json()["task_type"] == task_type.value

        listing = owner_client.get(f"/api/v1/tenants/{tenant_a}/ai/task-profiles")
        assert listing.status_code == 200
        assert {item["task_type"] for item in listing.json()} == {
            task_type.value for task_type in AITaskType
        }

        secret_parameter = owner_client.put(
            f"/api/v1/tenants/{tenant_a}/ai/task-profiles/customer_response",
            json={
                "routes": [
                    {
                        "provider": "openai",
                        "model_id": "safe-model",
                        "parameters": {"nested": {"api_key": "must-not-be-stored"}},
                    }
                ]
            },
        )
        assert secret_parameter.status_code == 422

    with TestClient(create_app(TEST_SETTINGS)) as tenant_b_client:
        login(tenant_b_client, "owner-b@example.com", "owner password tenant b")
        assert (
            tenant_b_client.get(
                f"/api/v1/tenants/{tenant_b}/ai/task-profiles/customer_response"
            ).status_code
            == 404
        )
        assert (
            tenant_b_client.get(
                f"/api/v1/tenants/{tenant_a}/ai/task-profiles/customer_response"
            ).status_code
            == 404
        )

    with TestClient(create_app(TEST_SETTINGS)) as viewer_client:
        login(viewer_client, "viewer-a@example.com", "viewer password tenant a")
        assert viewer_client.get(f"/api/v1/tenants/{tenant_a}/ai/task-profiles").status_code == 200
        forbidden = viewer_client.put(
            f"/api/v1/tenants/{tenant_a}/ai/task-profiles/customer_response",
            json={"routes": [{"provider": "openai", "model_id": "forbidden-model"}]},
        )
        assert forbidden.status_code == 403

    with TestClient(create_app(TEST_SETTINGS)) as admin_client:
        login(admin_client, "admin-a@example.com", "admin password tenant a")
        updated = admin_client.put(
            f"/api/v1/tenants/{tenant_a}/ai/task-profiles/customer_response",
            json={
                "timeout_seconds": 15,
                "attempts_per_route": 1,
                "routes": [
                    {"provider": "gemini", "model_id": "admin-selected-model"},
                    {"provider": "openai", "model_id": "fallback-model"},
                ],
            },
        )
        assert updated.status_code == 200
        assert [route["provider"] for route in updated.json()["routes"]] == ["gemini", "openai"]

    with Session(SYNC_ENGINE) as db:
        audit_actions = list(
            db.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.tenant_id == tenant_a,
                    AuditEvent.action == "ai.task_profile.updated",
                )
            ).all()
        )
        assert len(audit_actions) == len(AITaskType) + 1


def test_gateway_retries_falls_back_and_persists_normalized_attempt_traces() -> None:
    tenant_id, _ = seed_tenant_user(
        tenant_slug="routing-tenant",
        tenant_name="Routing Tenant",
        email="owner@example.com",
        password="owner password 1234",
    )
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        profile = AITaskProfile(
            tenant_id=tenant_id,
            task_type=AITaskType.CUSTOMER_RESPONSE.value,
            timeout_seconds=5,
            attempts_per_route=2,
        )
        db.add(profile)
        db.flush()
        db.add_all(
            [
                AITaskRoute(
                    tenant_id=tenant_id,
                    profile_id=profile.id,
                    provider="primary-mock",
                    model_id="primary-model",
                    priority=0,
                    parameters={},
                ),
                AITaskRoute(
                    tenant_id=tenant_id,
                    profile_id=profile.id,
                    provider="fallback-mock",
                    model_id="fallback-model",
                    priority=1,
                    parameters={},
                ),
            ]
        )
        db.commit()

    primary = MockAIProviderAdapter(key="primary-mock", failures_before_success=2)
    fallback = MockAIProviderAdapter(key="fallback-mock", generation_text="fallback answer")
    registry = AIProviderRegistry([primary, fallback])
    async_engine, session_factory = create_database(TEST_SETTINGS)
    gateway = AIGateway(registry, session_factory)

    async def run() -> None:
        try:
            result = await gateway.generate(
                tenant_id,
                AITaskType.CUSTOMER_RESPONSE,
                GenerationRequest(input_text="Where is my order?"),
            )
            assert result.provider == "fallback-mock"
            assert result.model_id == "fallback-model"
            assert result.text == "fallback answer"
        finally:
            await async_engine.dispose()

    asyncio.run(run())
    assert primary.calls == 2
    assert fallback.calls == 1

    with Session(SYNC_ENGINE) as db:
        traces = list(
            db.scalars(
                select(AIExecutionTrace)
                .where(AIExecutionTrace.tenant_id == tenant_id)
                .order_by(AIExecutionTrace.created_at, AIExecutionTrace.id)
            ).all()
        )
        assert [(trace.provider, trace.status, trace.attempt_number) for trace in traces] == [
            ("primary-mock", "failed", 1),
            ("primary-mock", "failed", 2),
            ("fallback-mock", "succeeded", 1),
        ]
        assert traces[-1].input_tokens == 1
        assert traces[-1].output_tokens == 1
        assert traces[-1].total_tokens == 2
        assert traces[0].error_code == "mock_retryable_failure"


def test_retryable_failure_is_not_masked_by_later_unavailable_route() -> None:
    tenant_id, _ = seed_tenant_user(
        tenant_slug="retryability-tenant",
        tenant_name="Retryability Tenant",
        email="retryability@example.com",
        password="retryability password",
    )
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        profile = AITaskProfile(
            tenant_id=tenant_id,
            task_type=AITaskType.CUSTOMER_RESPONSE.value,
            timeout_seconds=5,
            attempts_per_route=1,
        )
        db.add(profile)
        db.flush()
        db.add_all(
            [
                AITaskRoute(
                    tenant_id=tenant_id,
                    profile_id=profile.id,
                    provider="retryable-mock",
                    model_id="retryable-model",
                    priority=0,
                    parameters={},
                ),
                AITaskRoute(
                    tenant_id=tenant_id,
                    profile_id=profile.id,
                    provider="missing-provider",
                    model_id="missing-model",
                    priority=1,
                    parameters={},
                ),
            ]
        )
        db.commit()

    retryable = MockAIProviderAdapter(key="retryable-mock", failures_before_success=1)
    async_engine, session_factory = create_database(TEST_SETTINGS)
    gateway = AIGateway(AIProviderRegistry([retryable]), session_factory)

    async def run() -> None:
        try:
            with pytest.raises(AIRoutingError) as raised:
                await gateway.generate(
                    tenant_id,
                    AITaskType.CUSTOMER_RESPONSE,
                    GenerationRequest(input_text="Please retry later"),
                )
            assert raised.value.code == "provider_unavailable"
            assert raised.value.retryable is True
        finally:
            await async_engine.dispose()

    asyncio.run(run())
