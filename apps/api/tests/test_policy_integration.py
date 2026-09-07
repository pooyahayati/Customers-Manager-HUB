import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from customers_manager_hub.agent_models import AgentPromptVersion, AgentRun
from customers_manager_hub.channel_models import (
    ChannelAccount,
    ChannelInboundEvent,
    ChannelInboundEventStatus,
    ChannelType,
)
from customers_manager_hub.config import Settings
from customers_manager_hub.database import create_database
from customers_manager_hub.handoff_models import HandoffPolicy
from customers_manager_hub.main import create_app
from customers_manager_hub.models import (
    Contact,
    Conversation,
    Message,
    MessageAuthorType,
    MessageDirection,
    MessageType,
    PlatformUser,
    Tenant,
    TenantMembership,
    TenantRole,
)
from customers_manager_hub.policy_models import (
    OutsideBusinessHoursAction,
    PolicyAutonomyMode,
    PolicyDecisionAction,
    PolicyDecisionTrace,
    PolicyMessageAction,
    TenantPolicy,
)
from customers_manager_hub.policy_runtime import (
    BusinessWindow,
    EffectiveTenantPolicy,
    PolicyEngine,
    is_within_business_hours,
)
from customers_manager_hub.security import hash_password
from customers_manager_hub.tool_models import (
    AgentToolPermission,
    ToolAdapterKind,
    ToolExecution,
    ToolExecutionStatus,
    ToolRiskLevel,
)
from customers_manager_hub.tool_runtime import (
    ToolAdapterRegistry,
    ToolAdapterRequest,
    ToolRuntime,
    ToolRuntimeError,
)

RUN_DB_INTEGRATION = os.environ.get("RUN_DB_INTEGRATION") == "1"
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://cmh:change-me@localhost:5432/customers_manager_hub",
)
TEST_SETTINGS = Settings(app_env="test", database_url=DATABASE_URL)
SYNC_ENGINE = create_engine(TEST_SETTINGS.sqlalchemy_database_url)

pytestmark = pytest.mark.skipif(
    not RUN_DB_INTEGRATION,
    reason="Policy integration tests require RUN_DB_INTEGRATION=1",
)


class RecordingBusinessAdapter:
    kind = ToolAdapterKind.BUSINESS_REFERENCE

    def __init__(self) -> None:
        self.calls: list[ToolAdapterRequest] = []

    async def execute(self, request: ToolAdapterRequest) -> dict[str, object]:
        self.calls.append(request)
        return {"ok": True}


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


def object_schema(properties: dict[str, object], required: list[str]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def create_prompt_agent_tool(client: TestClient, tenant_id: UUID) -> tuple[UUID, UUID]:
    prompt = client.post(
        f"/api/v1/tenants/{tenant_id}/prompts",
        json={"name": "Policy Prompt", "content": "Use tools only when application policy allows."},
    )
    assert prompt.status_code == 201
    prompt_id = UUID(prompt.json()["id"])
    published = client.post(f"/api/v1/tenants/{tenant_id}/prompts/{prompt_id}/publish")
    assert published.status_code == 200
    agent = client.post(
        f"/api/v1/tenants/{tenant_id}/agents",
        json={"name": "Policy Agent", "prompt_id": str(prompt_id)},
    )
    assert agent.status_code == 201
    tool = client.post(
        f"/api/v1/tenants/{tenant_id}/tools",
        json={
            "name": "policy.lookup",
            "version": 1,
            "description": "Policy integration tool.",
            "adapter_kind": "business_reference",
            "operation_type": "read",
            "risk_level": "low",
            "input_schema": object_schema({"sku": {"type": "string"}}, ["sku"]),
            "output_schema": object_schema({"ok": {"type": "boolean"}}, ["ok"]),
            "configuration": {
                "url": "https://api.example.test/policy",
                "method": "GET",
                "argument_location": "query",
            },
            "timeout_seconds": 5,
            "max_attempts": 1,
            "requires_approval": False,
        },
    )
    assert tool.status_code == 201
    return UUID(agent.json()["id"]), UUID(tool.json()["id"])


def seed_event(tenant_id: UUID, *, message_text: str = "Please connect me to a human") -> UUID:
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        contact = Contact(tenant_id=tenant_id, display_name="Policy Customer")
        db.add(contact)
        db.flush()
        conversation = Conversation(tenant_id=tenant_id, contact_id=contact.id)
        account = ChannelAccount(
            tenant_id=tenant_id,
            channel_type=ChannelType.WEBSITE.value,
            name="Policy Website",
            external_account_id=f"policy-{uuid4()}",
        )
        db.add_all((conversation, account))
        db.flush()
        message = Message(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            direction=MessageDirection.INBOUND.value,
            author_type=MessageAuthorType.CUSTOMER.value,
            message_type=MessageType.TEXT.value,
            text=message_text,
            occurred_at=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        )
        db.add(message)
        db.flush()
        event = ChannelInboundEvent(
            tenant_id=tenant_id,
            channel_account_id=account.id,
            external_event_id=f"policy-event-{uuid4()}",
            event_type="message",
            status=ChannelInboundEventStatus.ENQUEUED.value,
            message_id=message.id,
        )
        db.add(event)
        db.commit()
        return event.id


def seed_agent_run(tenant_id: UUID, agent_id: UUID) -> UUID:
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        version = db.scalar(
            select(AgentPromptVersion).where(
                AgentPromptVersion.tenant_id == tenant_id,
                AgentPromptVersion.status == "published",
            )
        )
        assert version is not None
        contact = Contact(tenant_id=tenant_id, display_name="Tool Policy Customer")
        account = ChannelAccount(
            tenant_id=tenant_id,
            channel_type=ChannelType.WEBSITE.value,
            name="Tool Policy Website",
            external_account_id=f"tool-policy-{uuid4()}",
        )
        db.add_all((contact, account))
        db.flush()
        conversation = Conversation(tenant_id=tenant_id, contact_id=contact.id)
        db.add(conversation)
        db.flush()
        message = Message(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            direction=MessageDirection.INBOUND.value,
            author_type=MessageAuthorType.CUSTOMER.value,
            message_type=MessageType.TEXT.value,
            text="Check inventory",
            occurred_at=datetime.now(UTC),
        )
        db.add(message)
        db.flush()
        run = AgentRun(
            tenant_id=tenant_id,
            agent_id=agent_id,
            prompt_version_id=version.id,
            channel_account_id=account.id,
            conversation_id=conversation.id,
            inbound_message_id=message.id,
        )
        db.add(run)
        db.commit()
        return run.id


def test_policy_admin_rbac_tenant_isolation_and_legacy_sync() -> None:
    tenant_a, _ = seed_tenant_user(
        slug="policy-a",
        email="owner-a@example.com",
        password="owner-a-password",
    )
    seed_tenant_user(
        slug="policy-a-agent",
        email="agent-a@example.com",
        password="agent-a-password",
        role=TenantRole.AGENT,
        tenant_id=tenant_a,
    )
    tenant_b, _ = seed_tenant_user(
        slug="policy-b",
        email="owner-b@example.com",
        password="owner-b-password",
    )

    with TestClient(create_app(TEST_SETTINGS)) as client:
        login(client, "owner-a@example.com", "owner-a-password")
        default = client.get(f"/api/v1/tenants/{tenant_a}/policies")
        assert default.status_code == 200
        assert default.json()["revision"] == 0
        assert default.json()["autonomy_mode"] == "autonomous"

        updated = client.put(
            f"/api/v1/tenants/{tenant_a}/policies",
            json={
                "enabled": True,
                "timezone": "Europe/Amsterdam",
                "business_hours": {"mon": [{"start": "09:00", "end": "17:00"}]},
                "outside_business_hours_action": "handoff",
                "autonomy_mode": "autonomous",
                "message_rules": {"voice": "handoff"},
                "handoff_keywords": ["human please"],
                "handoff_on_tool_approval": False,
                "approval_min_risk": "medium",
                "require_approval_for_writes": True,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["revision"] == 1
        assert updated.json()["timezone"] == "Europe/Amsterdam"

        with Session(SYNC_ENGINE) as db:
            legacy = db.get(HandoffPolicy, tenant_a)
            assert legacy is not None
            assert legacy.customer_keywords == ["human please"]
            assert legacy.pause_on_tool_approval is False

        login(client, "agent-a@example.com", "agent-a-password")
        forbidden = client.put(
            f"/api/v1/tenants/{tenant_a}/policies",
            json={"enabled": False},
        )
        assert forbidden.status_code == 403

        login(client, "owner-a@example.com", "owner-a-password")
        _, tool_id = create_prompt_agent_tool(client, tenant_a)

        login(client, "owner-b@example.com", "owner-b-password")
        cross_rule = client.put(
            f"/api/v1/tenants/{tenant_b}/policies/tool-rules/{tool_id}",
            json={"effect": "deny", "approval_mode": "inherit"},
        )
        assert cross_rule.status_code == 404


def test_business_hours_and_message_autonomy_decisions_are_deterministic_and_safe() -> None:
    policy = EffectiveTenantPolicy(
        enabled=True,
        revision=3,
        timezone="Europe/Amsterdam",
        business_hours={"mon": (BusinessWindow(9 * 60, 17 * 60),)},
        outside_business_hours_action=OutsideBusinessHoursAction.HANDOFF,
        autonomy_mode=PolicyAutonomyMode.AUTONOMOUS,
        message_rules={MessageType.TEXT: PolicyMessageAction.ALLOW},
        handoff_keywords=(),
        handoff_on_tool_approval=True,
        approval_min_risk=ToolRiskLevel.HIGH,
        require_approval_for_writes=False,
    )
    assert is_within_business_hours(policy, datetime(2026, 9, 7, 10, 0, tzinfo=UTC)) is True
    assert is_within_business_hours(policy, datetime(2026, 9, 7, 18, 0, tzinfo=UTC)) is False

    tenant_id, _ = seed_tenant_user(
        slug="policy-runtime",
        email="owner@example.com",
        password="owner-password",
    )
    event_id = seed_event(tenant_id, message_text="SECRET MESSAGE human please")
    with Session(SYNC_ENGINE) as db:
        db.add(
            TenantPolicy(
                tenant_id=tenant_id,
                enabled=True,
                revision=4,
                timezone="UTC",
                business_hours={},
                outside_business_hours_action="allow",
                autonomy_mode="autonomous",
                message_rules={"text": "handoff"},
                handoff_keywords=["human please"],
                handoff_on_tool_approval=True,
                approval_min_risk="high",
                require_approval_for_writes=False,
            )
        )
        db.commit()

    _, session_factory = create_database(TEST_SETTINGS)
    engine = PolicyEngine(session_factory)
    message_decision = asyncio.run(engine.evaluate_message_event(event_id))
    assert message_decision.action == PolicyDecisionAction.HANDOFF
    assert message_decision.reason_code == "message_policy_handoff"

    with Session(SYNC_ENGINE) as db:
        row = db.get(TenantPolicy, tenant_id)
        assert row is not None
        row.message_rules = {"text": "allow"}
        row.autonomy_mode = "assist_only"
        row.revision += 1
        db.commit()

    autonomy_decision = asyncio.run(engine.evaluate_ai_event(event_id))
    assert autonomy_decision.action == PolicyDecisionAction.HANDOFF
    assert autonomy_decision.reason_code == "autonomy_assist_only"

    with Session(SYNC_ENGINE) as db:
        traces = list(
            db.scalars(
                select(PolicyDecisionTrace).where(PolicyDecisionTrace.tenant_id == tenant_id)
            )
        )
        assert len(traces) >= 2
        serialized = repr([trace.safe_context for trace in traces])
        assert "SECRET MESSAGE" not in serialized
        assert all(len(trace.safe_context) <= 20 for trace in traces)


def test_tool_policy_denies_strengthens_approval_and_never_grants_permission() -> None:
    tenant_id, _ = seed_tenant_user(
        slug="policy-tool",
        email="tool-owner@example.com",
        password="tool-owner-password",
    )
    with TestClient(create_app(TEST_SETTINGS)) as client:
        login(client, "tool-owner@example.com", "tool-owner-password")
        agent_id, tool_id = create_prompt_agent_tool(client, tenant_id)
        policy = client.put(
            f"/api/v1/tenants/{tenant_id}/policies",
            json={
                "enabled": True,
                "timezone": "UTC",
                "business_hours": {},
                "outside_business_hours_action": "allow",
                "autonomy_mode": "autonomous",
                "message_rules": {},
                "handoff_keywords": [],
                "handoff_on_tool_approval": True,
                "approval_min_risk": "low",
                "require_approval_for_writes": False,
            },
        )
        assert policy.status_code == 200
        rule = client.put(
            f"/api/v1/tenants/{tenant_id}/policies/tool-rules/{tool_id}",
            json={"effect": "allow", "approval_mode": "inherit"},
        )
        assert rule.status_code == 200

    _, session_factory = create_database(TEST_SETTINGS)
    policy_engine = PolicyEngine(session_factory)
    adapter = RecordingBusinessAdapter()
    runtime = ToolRuntime(
        TEST_SETTINGS,
        session_factory,
        ToolAdapterRegistry((adapter,)),
        policy_engine=policy_engine,
    )

    with pytest.raises(ToolRuntimeError) as unauthorized:
        asyncio.run(
            runtime.execute_agent_tool(
                tenant_id=tenant_id,
                agent_id=agent_id,
                agent_run_id=uuid4(),
                call_ordinal=1,
                qualified_name="policy.lookup@1",
                arguments={"sku": "A-1"},
            )
        )
    assert unauthorized.value.code == "tool_not_authorized"
    assert adapter.calls == []

    with Session(SYNC_ENGINE) as db:
        db.add(AgentToolPermission(tenant_id=tenant_id, agent_id=agent_id, tool_id=tool_id))
        db.commit()
    run_id = seed_agent_run(tenant_id, agent_id)

    approval = asyncio.run(
        runtime.execute_agent_tool(
            tenant_id=tenant_id,
            agent_id=agent_id,
            agent_run_id=run_id,
            call_ordinal=1,
            qualified_name="policy.lookup@1",
            arguments={"sku": "A-1"},
        )
    )
    assert approval.status == ToolExecutionStatus.APPROVAL_REQUIRED
    assert adapter.calls == []

    with TestClient(create_app(TEST_SETTINGS)) as client:
        login(client, "tool-owner@example.com", "tool-owner-password")
        denied_rule = client.put(
            f"/api/v1/tenants/{tenant_id}/policies/tool-rules/{tool_id}",
            json={"effect": "deny", "approval_mode": "inherit"},
        )
        assert denied_rule.status_code == 200

    denied = asyncio.run(
        runtime.execute_agent_tool(
            tenant_id=tenant_id,
            agent_id=agent_id,
            agent_run_id=run_id,
            call_ordinal=2,
            qualified_name="policy.lookup@1",
            arguments={"sku": "A-2"},
        )
    )
    assert denied.status == ToolExecutionStatus.DENIED
    assert denied.error_code == "tool_policy_denied"
    assert adapter.calls == []

    with Session(SYNC_ENGINE) as db:
        execution = db.scalar(
            select(ToolExecution).where(
                ToolExecution.tenant_id == tenant_id,
                ToolExecution.agent_run_id == run_id,
                ToolExecution.call_ordinal == 2,
            )
        )
        assert execution is not None
        assert execution.status == ToolExecutionStatus.DENIED.value
        traces = list(
            db.scalars(
                select(PolicyDecisionTrace).where(
                    PolicyDecisionTrace.tenant_id == tenant_id,
                    PolicyDecisionTrace.tool_id == tool_id,
                )
            )
        )
        assert any(trace.action == "approval_required" for trace in traces)
        assert any(trace.action == "deny" for trace in traces)
