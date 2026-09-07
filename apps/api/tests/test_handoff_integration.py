import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from customers_manager_hub.agent_models import (
    Agent,
    AgentChannelAssignment,
    AgentPrompt,
    AgentPromptVersion,
    AgentRun,
    AgentRunStatus,
    PromptVersionStatus,
)
from customers_manager_hub.agent_runtime import process_agent_event
from customers_manager_hub.ai_gateway import AIGateway, AIProviderRegistry, MockAIProviderAdapter
from customers_manager_hub.ai_models import AITaskProfile, AITaskRoute, AITaskType
from customers_manager_hub.channel_gateway import ChannelRegistry
from customers_manager_hub.channel_models import (
    ChannelAccount,
    ChannelInboundEvent,
    ChannelInboundEventStatus,
    ChannelType,
    ConversationChannelBinding,
)
from customers_manager_hub.config import Settings
from customers_manager_hub.database import create_database
from customers_manager_hub.handoff_models import (
    ConversationHandoff,
    HandoffPolicy,
    HandoffStatus,
    OperatorAssistSuggestion,
)
from customers_manager_hub.handoff_runtime import (
    evaluate_event_escalation,
    pause_for_tool_approval,
    resolve_handoff,
)
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
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
TEST_SETTINGS = Settings(app_env="test", database_url=DATABASE_URL, redis_url=REDIS_URL)
SYNC_ENGINE = create_engine(TEST_SETTINGS.sqlalchemy_database_url)
ASYNC_ENGINE, SESSION_FACTORY = create_database(TEST_SETTINGS)

pytestmark = pytest.mark.skipif(
    not RUN_DB_INTEGRATION,
    reason="Handoff integration tests require RUN_DB_INTEGRATION=1",
)


@pytest.fixture(autouse=True)
def clean_handoff_database() -> Iterator[None]:
    with SYNC_ENGINE.begin() as connection:
        connection.execute(text("TRUNCATE TABLE platform_users, tenants CASCADE"))
    yield
    with SYNC_ENGINE.begin() as connection:
        connection.execute(text("TRUNCATE TABLE platform_users, tenants CASCADE"))


def seed_user(
    *,
    slug: str,
    email: str,
    password: str,
    role: TenantRole,
    tenant_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        if tenant_id is None:
            tenant = Tenant(slug=slug, name=slug.replace("-", " ").title())
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


def seed_conversation_bundle(tenant_id: UUID, *, text_value: str = "I need help") -> tuple[UUID, UUID, UUID, UUID]:
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        contact = Contact(tenant_id=tenant_id, display_name="Customer")
        db.add(contact)
        db.flush()
        conversation = Conversation(
            tenant_id=tenant_id,
            contact_id=contact.id,
            subject="Support",
            last_message_at=datetime.now(UTC),
        )
        db.add(conversation)
        db.flush()
        account = ChannelAccount(
            tenant_id=tenant_id,
            channel_type=ChannelType.WEBSITE.value,
            name="Storefront",
            external_account_id=f"website-{uuid4()}",
            is_active=True,
        )
        db.add(account)
        db.flush()
        db.add(
            ConversationChannelBinding(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                channel_account_id=account.id,
                external_thread_id=f"thread-{uuid4()}",
            )
        )
        message = Message(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            direction=MessageDirection.INBOUND.value,
            author_type=MessageAuthorType.CUSTOMER.value,
            message_type=MessageType.TEXT.value,
            text=text_value,
            external_message_id=f"in-{uuid4()}",
            idempotency_key=f"inbound:{uuid4()}",
            external_metadata={},
            occurred_at=datetime.now(UTC),
        )
        db.add(message)
        db.flush()
        event = ChannelInboundEvent(
            tenant_id=tenant_id,
            channel_account_id=account.id,
            external_event_id=f"event-{uuid4()}",
            event_type="message",
            status=ChannelInboundEventStatus.PROCESSED.value,
            message_id=message.id,
        )
        db.add(event)
        db.commit()
        return conversation.id, account.id, message.id, event.id


def seed_agent_stack(tenant_id: UUID, account_id: UUID) -> tuple[UUID, UUID]:
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        prompt = AgentPrompt(tenant_id=tenant_id, name="Support Prompt")
        db.add(prompt)
        db.flush()
        version = AgentPromptVersion(
            tenant_id=tenant_id,
            prompt_id=prompt.id,
            version=1,
            status=PromptVersionStatus.PUBLISHED.value,
            content="Help the customer accurately and concisely.",
            published_at=datetime.now(UTC),
        )
        agent = Agent(
            tenant_id=tenant_id,
            prompt_id=prompt.id,
            name="Support Agent",
            is_active=True,
        )
        db.add_all((version, agent))
        db.flush()
        db.add(
            AgentChannelAssignment(
                tenant_id=tenant_id,
                agent_id=agent.id,
                channel_account_id=account_id,
            )
        )
        profile = AITaskProfile(
            tenant_id=tenant_id,
            task_type=AITaskType.CUSTOMER_RESPONSE.value,
            timeout_seconds=30,
            attempts_per_route=1,
        )
        db.add(profile)
        db.flush()
        db.add(
            AITaskRoute(
                tenant_id=tenant_id,
                profile_id=profile.id,
                provider="mock",
                model_id="mock-handoff-model",
                priority=0,
                parameters={},
            )
        )
        db.commit()
        return agent.id, version.id


def login(client: TestClient, email: str, password: str) -> Response:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response


def test_operator_lifecycle_rbac_reply_idempotency_and_tenant_isolation() -> None:
    tenant_a, owner_id = seed_user(
        slug="handoff-a",
        email="owner-a@example.com",
        password="owner handoff password a",
        role=TenantRole.OWNER,
    )
    _, agent_user_id = seed_user(
        slug="ignored-a",
        email="agent-a@example.com",
        password="agent handoff password a",
        role=TenantRole.AGENT,
        tenant_id=tenant_a,
    )
    seed_user(
        slug="ignored-viewer-a",
        email="viewer-a@example.com",
        password="viewer handoff password a",
        role=TenantRole.VIEWER,
        tenant_id=tenant_a,
    )
    tenant_b, _ = seed_user(
        slug="handoff-b",
        email="owner-b@example.com",
        password="owner handoff password b",
        role=TenantRole.OWNER,
    )
    conversation_id, _, _, _ = seed_conversation_bundle(tenant_a)

    with TestClient(create_app(TEST_SETTINGS)) as client:
        login(client, "owner-a@example.com", "owner handoff password a")
        created = client.post(
            f"/api/v1/tenants/{tenant_a}/operator-inbox/{conversation_id}/handoff",
            json={"reason_code": "manual_request", "reason_text": "Customer requested a person"},
        )
        assert created.status_code == 201
        handoff_id = created.json()["id"]
        assert created.json()["status"] == "queued"
        assert created.json()["requested_by_user_id"] == str(owner_id)

        replay = client.post(
            f"/api/v1/tenants/{tenant_a}/operator-inbox/{conversation_id}/handoff",
            json={"reason_code": "manual_request"},
        )
        assert replay.status_code == 201
        assert replay.json()["id"] == handoff_id

        login(client, "viewer-a@example.com", "viewer handoff password a")
        denied = client.put(
            f"/api/v1/tenants/{tenant_a}/operator-inbox/{conversation_id}/claim"
        )
        assert denied.status_code == 403

        login(client, "agent-a@example.com", "agent handoff password a")
        claimed = client.put(
            f"/api/v1/tenants/{tenant_a}/operator-inbox/{conversation_id}/claim"
        )
        assert claimed.status_code == 200
        assert claimed.json()["status"] == "claimed"
        assert claimed.json()["claimed_by_user_id"] == str(agent_user_id)

        message_key = uuid4()
        sent = client.post(
            f"/api/v1/tenants/{tenant_a}/operator-inbox/{conversation_id}/reply",
            json={"client_message_id": str(message_key), "text": "A human is handling this now."},
        )
        assert sent.status_code == 200
        retry = client.post(
            f"/api/v1/tenants/{tenant_a}/operator-inbox/{conversation_id}/reply",
            json={"client_message_id": str(message_key), "text": "A human is handling this now."},
        )
        assert retry.status_code == 200
        assert retry.json()["message_id"] == sent.json()["message_id"]

        with Session(SYNC_ENGINE) as db:
            outbound = db.get(Message, UUID(sent.json()["message_id"]))
            assert outbound is not None
            assert outbound.author_type == MessageAuthorType.HUMAN.value
            assert outbound.direction == MessageDirection.OUTBOUND.value
            assert outbound.idempotency_key == f"human:{handoff_id}:{message_key}"

        released = client.put(
            f"/api/v1/tenants/{tenant_a}/operator-inbox/{conversation_id}/release"
        )
        assert released.status_code == 200
        assert released.json()["status"] == "queued"

        login(client, "owner-b@example.com", "owner handoff password b")
        cross_tenant = client.get(
            f"/api/v1/tenants/{tenant_b}/operator-inbox/{conversation_id}"
        )
        assert cross_tenant.status_code == 404

        login(client, "owner-a@example.com", "owner handoff password a")
        owner_claim = client.put(
            f"/api/v1/tenants/{tenant_a}/operator-inbox/{conversation_id}/claim"
        )
        assert owner_claim.status_code == 200
        resolved = client.put(
            f"/api/v1/tenants/{tenant_a}/operator-inbox/{conversation_id}/resolve",
            json={"resume_ai": False, "cancel": False},
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "resolved"


def test_active_handoff_blocks_agent_and_assist_is_draft_only() -> None:
    tenant_id, _ = seed_user(
        slug="handoff-ai",
        email="owner@example.com",
        password="owner handoff ai password",
        role=TenantRole.OWNER,
    )
    conversation_id, account_id, _, event_id = seed_conversation_bundle(
        tenant_id, text_value="Can you help with my order?"
    )
    seed_agent_stack(tenant_id, account_id)
    provider = MockAIProviderAdapter(key="mock", generation_text="Suggested human draft")

    app = create_app(TEST_SETTINGS)
    with TestClient(app) as client:
        app.state.ai_gateway = AIGateway(
            AIProviderRegistry((provider,)), app.state.db_session_factory
        )
        login(client, "owner@example.com", "owner handoff ai password")
        assert (
            client.post(
                f"/api/v1/tenants/{tenant_id}/operator-inbox/{conversation_id}/handoff",
                json={"reason_code": "manual"},
            ).status_code
            == 201
        )
        assert (
            client.put(
                f"/api/v1/tenants/{tenant_id}/operator-inbox/{conversation_id}/claim"
            ).status_code
            == 200
        )

        asyncio.run(
            process_agent_event(
                app.state.db_session_factory,
                app.state.ai_gateway,
                ChannelRegistry(()),
                TEST_SETTINGS,
                event_id,
            )
        )
        assert provider.calls == 0
        with Session(SYNC_ENGINE) as db:
            assert db.scalar(select(AgentRun).where(AgentRun.tenant_id == tenant_id)) is None

        assist = client.post(
            f"/api/v1/tenants/{tenant_id}/operator-inbox/{conversation_id}/assist"
        )
        assert assist.status_code == 201
        assert assist.json()["text"] == "Suggested human draft"
        assert provider.calls == 1

        with Session(SYNC_ENGINE) as db:
            suggestion = db.get(OperatorAssistSuggestion, UUID(assist.json()["id"]))
            assert suggestion is not None
            assert suggestion.text == "Suggested human draft"
            outbound_count = len(
                db.scalars(
                    select(Message).where(
                        Message.tenant_id == tenant_id,
                        Message.conversation_id == conversation_id,
                        Message.direction == MessageDirection.OUTBOUND.value,
                    )
                ).all()
            )
            assert outbound_count == 0


def test_keyword_escalation_and_tool_approval_pause_resume_are_separate_controls() -> None:
    tenant_id, operator_id = seed_user(
        slug="handoff-runtime",
        email="operator@example.com",
        password="operator handoff runtime password",
        role=TenantRole.OWNER,
    )
    conversation_id, account_id, message_id, event_id = seed_conversation_bundle(
        tenant_id, text_value="Please connect me to a human agent"
    )
    agent_id, prompt_version_id = seed_agent_stack(tenant_id, account_id)

    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        db.add(
            HandoffPolicy(
                tenant_id=tenant_id,
                enabled=True,
                customer_keywords=["human agent"],
                pause_on_tool_approval=True,
            )
        )
        db.commit()

    first_handoff_id = asyncio.run(evaluate_event_escalation(SESSION_FACTORY, event_id))
    assert first_handoff_id is not None
    second_handoff_id = asyncio.run(evaluate_event_escalation(SESSION_FACTORY, event_id))
    assert second_handoff_id == first_handoff_id

    resolution = asyncio.run(
        resolve_handoff(
            SESSION_FACTORY,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            actor_user_id=operator_id,
            allow_override=True,
            resume_ai=False,
        )
    )
    assert resolution.event_id_to_resume is None

    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        run = AgentRun(
            tenant_id=tenant_id,
            agent_id=agent_id,
            prompt_version_id=prompt_version_id,
            channel_account_id=account_id,
            conversation_id=conversation_id,
            inbound_message_id=message_id,
            status=AgentRunStatus.PENDING.value,
        )
        db.add(run)
        tool = ToolDefinition(
            tenant_id=tenant_id,
            name="dangerous.action",
            version=1,
            description="High risk approval test",
            adapter_kind=ToolAdapterKind.BUSINESS_REFERENCE.value,
            operation_type=ToolOperationType.READ.value,
            risk_level=ToolRiskLevel.HIGH.value,
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_schema={"type": "object", "properties": {}, "additionalProperties": False},
            configuration={
                "url": "https://api.example.test/reference",
                "method": "GET",
                "argument_location": "query",
            },
            timeout_seconds=5,
            max_attempts=1,
            requires_approval=True,
            is_active=True,
        )
        db.add(tool)
        db.flush()
        execution = ToolExecution(
            tenant_id=tenant_id,
            tool_id=tool.id,
            agent_id=agent_id,
            agent_run_id=run.id,
            call_ordinal=1,
            idempotency_key=f"handoff-tool:{run.id}:1",
            status=ToolExecutionStatus.APPROVAL_REQUIRED.value,
            approval_status=ToolApprovalStatus.PENDING.value,
            input_payload={},
        )
        db.add(execution)
        db.commit()
        db.refresh(run)
        db.refresh(execution)
        run_id = run.id
        execution_id = execution.id

    tool_handoff_id = asyncio.run(
        pause_for_tool_approval(
            SESSION_FACTORY,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            agent_run_id=run_id,
            tool_execution_id=execution_id,
        )
    )
    assert tool_handoff_id is not None

    with Session(SYNC_ENGINE) as db:
        run = db.get(AgentRun, run_id)
        execution = db.get(ToolExecution, execution_id)
        handoff = db.get(ConversationHandoff, tool_handoff_id)
        assert run is not None and run.status == AgentRunStatus.PAUSED.value
        assert handoff is not None and handoff.status == HandoffStatus.QUEUED.value
        assert execution is not None
        assert execution.status == ToolExecutionStatus.APPROVAL_REQUIRED.value
        assert execution.approval_status == ToolApprovalStatus.PENDING.value

    resumed = asyncio.run(
        resolve_handoff(
            SESSION_FACTORY,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            actor_user_id=operator_id,
            allow_override=True,
            resume_ai=True,
        )
    )
    assert resumed.event_id_to_resume == event_id

    with Session(SYNC_ENGINE) as db:
        run = db.get(AgentRun, run_id)
        execution = db.get(ToolExecution, execution_id)
        assert run is not None and run.status == AgentRunStatus.PENDING.value
        assert execution is not None
        assert execution.approval_status == ToolApprovalStatus.PENDING.value
