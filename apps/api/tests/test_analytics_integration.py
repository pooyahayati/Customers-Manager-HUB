import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from customers_manager_hub.agent_models import Agent, AgentPrompt
from customers_manager_hub.ai_models import AIExecutionTrace
from customers_manager_hub.analytics_models import AIModelPricing
from customers_manager_hub.config import Settings
from customers_manager_hub.handoff_models import (
    ConversationHandoff,
    HandoffRequestSource,
    HandoffStatus,
)
from customers_manager_hub.main import create_app
from customers_manager_hub.models import (
    AuditEvent,
    Contact,
    Conversation,
    ConversationStatus,
    Message,
    MessageAuthorType,
    MessageDirection,
    MessageType,
    PlatformUser,
    Tenant,
    TenantMembership,
    TenantRole,
)
from customers_manager_hub.security import hash_password
from customers_manager_hub.tool_models import (
    ToolAdapterKind,
    ToolApprovalStatus,
    ToolDefinition,
    ToolExecution,
    ToolExecutionStatus,
    ToolOperationType,
    ToolRiskLevel,
)

RUN_DB_INTEGRATION = os.environ.get("RUN_DB_INTEGRATION") == "1"
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://cmh:change-me@localhost:5432/customers_manager_hub",
)
TEST_SETTINGS = Settings(app_env="test", database_url=DATABASE_URL)
SYNC_ENGINE = create_engine(TEST_SETTINGS.sqlalchemy_database_url)
BASE = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)

pytestmark = pytest.mark.skipif(
    not RUN_DB_INTEGRATION,
    reason="Analytics integration tests require RUN_DB_INTEGRATION=1",
)


@pytest.fixture(autouse=True)
def clean_database() -> Iterator[None]:
    with SYNC_ENGINE.begin() as connection:
        connection.execute(text("TRUNCATE TABLE platform_users, tenants CASCADE"))
    yield
    with SYNC_ENGINE.begin() as connection:
        connection.execute(text("TRUNCATE TABLE platform_users, tenants CASCADE"))


def seed_tenant_user(
    *,
    slug: str,
    email: str,
    password: str,
    role: TenantRole = TenantRole.OWNER,
    tenant_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        if tenant_id is None:
            tenant = Tenant(slug=slug, name=slug.title())
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


def login(client: TestClient, email: str, password: str) -> None:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200


def seed_operational_data(tenant_id: UUID) -> None:
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        contact_one = Contact(tenant_id=tenant_id, display_name="One", created_at=BASE)
        contact_two = Contact(tenant_id=tenant_id, display_name="Two", created_at=BASE)
        db.add_all((contact_one, contact_two))
        db.flush()

        automated = Conversation(
            tenant_id=tenant_id,
            contact_id=contact_one.id,
            status=ConversationStatus.OPEN.value,
            created_at=BASE,
            updated_at=BASE,
        )
        human = Conversation(
            tenant_id=tenant_id,
            contact_id=contact_two.id,
            status=ConversationStatus.RESOLVED.value,
            created_at=BASE,
            updated_at=BASE + timedelta(seconds=600),
        )
        db.add_all((automated, human))
        db.flush()

        db.add_all(
            (
                Message(
                    tenant_id=tenant_id,
                    conversation_id=automated.id,
                    direction=MessageDirection.INBOUND.value,
                    author_type=MessageAuthorType.CUSTOMER.value,
                    message_type=MessageType.TEXT.value,
                    text="customer payload must not appear in analytics",
                    occurred_at=BASE + timedelta(seconds=10),
                ),
                Message(
                    tenant_id=tenant_id,
                    conversation_id=automated.id,
                    direction=MessageDirection.OUTBOUND.value,
                    author_type=MessageAuthorType.AI.value,
                    message_type=MessageType.TEXT.value,
                    text="ai response",
                    occurred_at=BASE + timedelta(seconds=40),
                ),
                Message(
                    tenant_id=tenant_id,
                    conversation_id=human.id,
                    direction=MessageDirection.INBOUND.value,
                    author_type=MessageAuthorType.CUSTOMER.value,
                    message_type=MessageType.TEXT.value,
                    text="human please",
                    occurred_at=BASE + timedelta(seconds=20),
                ),
                Message(
                    tenant_id=tenant_id,
                    conversation_id=human.id,
                    direction=MessageDirection.OUTBOUND.value,
                    author_type=MessageAuthorType.HUMAN.value,
                    message_type=MessageType.TEXT.value,
                    text="human response",
                    occurred_at=BASE + timedelta(seconds=80),
                ),
            )
        )
        db.add(
            ConversationHandoff(
                tenant_id=tenant_id,
                conversation_id=human.id,
                status=HandoffStatus.RESOLVED.value,
                request_source=HandoffRequestSource.SYSTEM.value,
                reason_code="analytics_test",
                requested_at=BASE + timedelta(seconds=25),
                closed_at=BASE + timedelta(seconds=325),
                created_at=BASE + timedelta(seconds=25),
                updated_at=BASE + timedelta(seconds=325),
            )
        )

        prompt = AgentPrompt(tenant_id=tenant_id, name="Analytics prompt")
        db.add(prompt)
        db.flush()
        agent = Agent(tenant_id=tenant_id, prompt_id=prompt.id, name="Analytics agent")
        db.add(agent)
        db.flush()
        tool = ToolDefinition(
            tenant_id=tenant_id,
            name="analytics.lookup",
            version=1,
            description="Analytics integration tool",
            adapter_kind=ToolAdapterKind.BUSINESS_REFERENCE.value,
            operation_type=ToolOperationType.READ.value,
            risk_level=ToolRiskLevel.LOW.value,
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_schema={"type": "object", "properties": {}, "additionalProperties": True},
            configuration={"url": "https://api.example.test/analytics", "method": "GET"},
            timeout_seconds=5,
            max_attempts=1,
            requires_approval=False,
        )
        db.add(tool)
        db.flush()
        db.add_all(
            (
                ToolExecution(
                    tenant_id=tenant_id,
                    tool_id=tool.id,
                    agent_id=agent.id,
                    idempotency_key="analytics-tool-success",
                    status=ToolExecutionStatus.SUCCEEDED.value,
                    approval_status=ToolApprovalStatus.NOT_REQUIRED.value,
                    input_payload={"secret_payload": "must-not-leak"},
                    output_payload={"ok": True},
                    attempt_count=1,
                    duration_ms=100,
                    created_at=BASE + timedelta(minutes=10),
                    updated_at=BASE + timedelta(minutes=10),
                ),
                ToolExecution(
                    tenant_id=tenant_id,
                    tool_id=tool.id,
                    agent_id=agent.id,
                    idempotency_key="analytics-tool-failed",
                    status=ToolExecutionStatus.FAILED.value,
                    approval_status=ToolApprovalStatus.NOT_REQUIRED.value,
                    input_payload={"secret_payload": "must-not-leak"},
                    error_code="test_failure",
                    attempt_count=1,
                    duration_ms=300,
                    created_at=BASE + timedelta(minutes=20),
                    updated_at=BASE + timedelta(minutes=20),
                ),
            )
        )
        db.commit()


def seed_ai_usage(tenant_id: UUID) -> None:
    with Session(SYNC_ENGINE) as db:
        db.add_all(
            (
                AIExecutionTrace(
                    tenant_id=tenant_id,
                    task_profile_id=uuid4(),
                    task_type="customer_response",
                    provider="openai",
                    model_id="analytics-model",
                    route_priority=0,
                    attempt_number=1,
                    status="succeeded",
                    latency_ms=100,
                    input_tokens=1_000_000,
                    output_tokens=0,
                    total_tokens=1_000_000,
                    created_at=BASE + timedelta(minutes=30),
                ),
                AIExecutionTrace(
                    tenant_id=tenant_id,
                    task_profile_id=uuid4(),
                    task_type="customer_response",
                    provider="openai",
                    model_id="analytics-model",
                    route_priority=0,
                    attempt_number=1,
                    status="succeeded",
                    latency_ms=300,
                    input_tokens=1_000_000,
                    output_tokens=0,
                    total_tokens=1_000_000,
                    created_at=BASE + timedelta(hours=2),
                ),
                AIExecutionTrace(
                    tenant_id=tenant_id,
                    task_profile_id=uuid4(),
                    task_type="voice_transcription",
                    provider="openai",
                    model_id="analytics-model",
                    route_priority=0,
                    attempt_number=1,
                    status="succeeded",
                    latency_ms=200,
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    audio_seconds=120.0,
                    created_at=BASE + timedelta(hours=2, minutes=10),
                ),
                AIExecutionTrace(
                    tenant_id=tenant_id,
                    task_profile_id=uuid4(),
                    task_type="customer_response",
                    provider="unpriced",
                    model_id="unknown-model",
                    route_priority=0,
                    attempt_number=1,
                    status="failed",
                    latency_ms=50,
                    input_tokens=100,
                    output_tokens=0,
                    total_tokens=100,
                    error_code="provider_error",
                    created_at=BASE + timedelta(hours=3),
                ),
            )
        )
        db.commit()


def report_params() -> dict[str, str]:
    return {
        "from": (BASE - timedelta(hours=1)).isoformat(),
        "to": (BASE + timedelta(days=1)).isoformat(),
    }


def test_pricing_rbac_audit_and_tenant_isolation() -> None:
    tenant_id, _ = seed_tenant_user(
        slug="analytics-a",
        email="owner-a@example.test",
        password="AnalyticsPassA123",
    )
    seed_tenant_user(
        slug="unused",
        email="viewer-a@example.test",
        password="AnalyticsViewer123",
        role=TenantRole.VIEWER,
        tenant_id=tenant_id,
    )
    other_tenant_id, _ = seed_tenant_user(
        slug="analytics-b",
        email="owner-b@example.test",
        password="AnalyticsPassB123",
    )

    with TestClient(create_app(TEST_SETTINGS)) as client:
        login(client, "owner-a@example.test", "AnalyticsPassA123")
        created = client.put(
            f"/api/v1/tenants/{tenant_id}/analytics/pricing",
            json={
                "provider": "OpenAI",
                "model_id": "analytics-model",
                "input_per_million_usd": "1.25",
                "output_per_million_usd": "2.50",
                "audio_per_minute_usd": "0.20",
                "effective_from": BASE.isoformat(),
            },
        )
        assert created.status_code == 200
        assert created.json()["provider"] == "openai"

        listed = client.get(f"/api/v1/tenants/{tenant_id}/analytics/pricing")
        assert listed.status_code == 200
        assert len(listed.json()) == 1

        login(client, "viewer-a@example.test", "AnalyticsViewer123")
        denied = client.put(
            f"/api/v1/tenants/{tenant_id}/analytics/pricing",
            json={
                "provider": "openai",
                "model_id": "analytics-model",
                "input_per_million_usd": "9",
                "output_per_million_usd": "9",
                "audio_per_minute_usd": "9",
                "effective_from": BASE.isoformat(),
            },
        )
        assert denied.status_code == 403

        login(client, "owner-b@example.test", "AnalyticsPassB123")
        cross_tenant = client.get(f"/api/v1/tenants/{tenant_id}/analytics/pricing")
        assert cross_tenant.status_code in {403, 404}
        own_tenant = client.get(f"/api/v1/tenants/{other_tenant_id}/analytics/pricing")
        assert own_tenant.status_code == 200
        assert own_tenant.json() == []

    with Session(SYNC_ENGINE) as db:
        pricing = db.scalar(select(AIModelPricing).where(AIModelPricing.tenant_id == tenant_id))
        assert pricing is not None
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.tenant_id == tenant_id,
                AuditEvent.action == "analytics.pricing.created",
            )
        )
        assert audit is not None


def test_ai_usage_uses_effective_dated_pricing_and_reports_unpriced_usage() -> None:
    tenant_id, _ = seed_tenant_user(
        slug="analytics-cost",
        email="cost@example.test",
        password="AnalyticsCost123",
    )
    seed_ai_usage(tenant_id)

    with TestClient(create_app(TEST_SETTINGS)) as client:
        login(client, "cost@example.test", "AnalyticsCost123")
        for effective_from, input_rate in (
            (BASE - timedelta(days=1), "1"),
            (BASE + timedelta(hours=1), "2"),
        ):
            response = client.put(
                f"/api/v1/tenants/{tenant_id}/analytics/pricing",
                json={
                    "provider": "openai",
                    "model_id": "analytics-model",
                    "input_per_million_usd": input_rate,
                    "output_per_million_usd": "4",
                    "audio_per_minute_usd": "0.5",
                    "effective_from": effective_from.isoformat(),
                },
            )
            assert response.status_code == 200

        usage = client.get(
            f"/api/v1/tenants/{tenant_id}/analytics/ai-usage",
            params=report_params(),
        )
        assert usage.status_code == 200
        body = usage.json()
        assert body["requests"] == 4
        assert body["succeeded"] == 3
        assert body["failed"] == 1
        assert body["input_tokens"] == 2_000_100
        assert body["audio_seconds"] == 120.0
        assert Decimal(body["estimated_cost_usd"]) == Decimal("4.0")
        assert body["unpriced_requests"] == 1
        assert len(body["breakdown"]) == 3


def test_overview_and_tool_metrics_are_conversation_level_and_payload_safe() -> None:
    tenant_id, _ = seed_tenant_user(
        slug="analytics-ops",
        email="ops@example.test",
        password="AnalyticsOps123",
    )
    seed_operational_data(tenant_id)

    with TestClient(create_app(TEST_SETTINGS)) as client:
        login(client, "ops@example.test", "AnalyticsOps123")
        overview = client.get(
            f"/api/v1/tenants/{tenant_id}/analytics/overview",
            params=report_params(),
        )
        assert overview.status_code == 200
        body = overview.json()
        operations = body["operations"]
        assert operations["conversations"] == 2
        assert operations["unique_contacts"] == 2
        assert operations["first_response_samples"] == 2
        assert operations["first_response_average_seconds"] == 45.0
        assert operations["resolution_samples"] == 1
        assert operations["resolution_average_seconds"] == 600.0
        assert operations["handoff_conversations"] == 1
        assert operations["handoff_rate"] == 0.5
        assert operations["handoff_duration_samples"] == 1
        assert operations["handoff_average_seconds"] == 300.0
        assert operations["automated_conversations"] == 1
        assert operations["ai_automation_rate"] == 0.5

        tools = body["tools"]
        assert tools["executions"] == 2
        assert tools["succeeded"] == 1
        assert tools["failed"] == 1
        assert tools["success_rate"] == 0.5
        assert tools["tools"][0]["average_duration_ms"] == 200.0

        serialized = overview.text
        assert "customer payload must not appear in analytics" not in serialized
        assert "secret_payload" not in serialized
        assert "must-not-leak" not in serialized


def test_report_window_validation_rejects_naive_and_oversized_ranges() -> None:
    tenant_id, _ = seed_tenant_user(
        slug="analytics-window",
        email="window@example.test",
        password="AnalyticsWindow123",
    )
    with TestClient(create_app(TEST_SETTINGS)) as client:
        login(client, "window@example.test", "AnalyticsWindow123")
        naive = client.get(
            f"/api/v1/tenants/{tenant_id}/analytics/overview",
            params={"from": "2026-09-01T00:00:00", "to": "2026-09-02T00:00:00Z"},
        )
        assert naive.status_code == 422

        oversized = client.get(
            f"/api/v1/tenants/{tenant_id}/analytics/overview",
            params={
                "from": "2025-01-01T00:00:00Z",
                "to": "2026-09-07T00:00:00Z",
            },
        )
        assert oversized.status_code == 422
