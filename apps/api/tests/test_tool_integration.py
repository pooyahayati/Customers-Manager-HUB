import asyncio
import base64
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import SecretStr
from redis import Redis
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from customers_manager_hub.agent_models import AgentRun
from customers_manager_hub.agent_runtime import process_agent_event
from customers_manager_hub.ai_gateway import (
    AIGateway,
    AIProviderRegistry,
    GenerationRequest,
    GenerationResult,
    MockAIProviderAdapter,
)
from customers_manager_hub.ai_models import AITaskProfile, AITaskRoute, AITaskType
from customers_manager_hub.channel_gateway import (
    ChannelAccountIdentity,
    ChannelMediaReference,
    ChannelRegistry,
    ChannelSendResult,
)
from customers_manager_hub.channel_models import (
    ChannelAccount,
    ChannelCapability,
    ChannelCredential,
    ChannelCredentialKind,
    ChannelInboundEvent,
    ChannelType,
)
from customers_manager_hub.channel_queue import (
    CHANNEL_JOB_STREAM,
    ChannelJobQueue,
    create_channel_redis,
)
from customers_manager_hub.channel_security import decrypt_channel_secret
from customers_manager_hub.config import Settings
from customers_manager_hub.database import create_database
from customers_manager_hub.main import create_app
from customers_manager_hub.models import (
    AuditEvent,
    Message,
    PlatformUser,
    Tenant,
    TenantMembership,
    TenantRole,
)
from customers_manager_hub.security import hash_password
from customers_manager_hub.tool_models import (
    AgentToolPermission,
    ToolAdapterKind,
    ToolApprovalStatus,
    ToolCredential,
    ToolDefinition,
    ToolExecution,
    ToolExecutionStatus,
)
from customers_manager_hub.tool_runtime import (
    ToolAdapterRegistry,
    ToolAdapterRequest,
    ToolRuntime,
    ToolRuntimeError,
)
from customers_manager_hub.worker import process_job

RUN_DB_INTEGRATION = os.environ.get("RUN_DB_INTEGRATION") == "1"
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://cmh:change-me@localhost:5432/customers_manager_hub",
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
TEST_SETTINGS = Settings(
    app_env="test",
    database_url=DATABASE_URL,
    redis_url=REDIS_URL,
    encryption_key=SecretStr(base64.urlsafe_b64encode(b"t" * 32).decode()),
    telegram_webhook_base_url="https://tools.example.test",
)
SYNC_ENGINE = create_engine(TEST_SETTINGS.sqlalchemy_database_url)
SYNC_REDIS = Redis.from_url(REDIS_URL, decode_responses=True)  # pyright: ignore[reportUnknownMemberType]

pytestmark = pytest.mark.skipif(
    not RUN_DB_INTEGRATION,
    reason="Tool integration tests require RUN_DB_INTEGRATION=1",
)


class FakeTelegramAdapter:
    channel_type = ChannelType.TELEGRAM
    capabilities = frozenset(
        {
            ChannelCapability.TEXT,
            ChannelCapability.OUTBOUND_TEXT,
        }
    )

    def __init__(self) -> None:
        self.send_calls: list[tuple[str, str, str]] = []

    async def validate_account(self, access_secret: str) -> ChannelAccountIdentity:
        assert access_secret == "800001:tool-test-token"
        return ChannelAccountIdentity(
            external_account_id="800001",
            username="tool_test_bot",
            display_name="Tool Test Bot",
        )

    async def register_webhook(
        self,
        access_secret: str,
        *,
        webhook_url: str,
        webhook_secret: str,
    ) -> None:
        del access_secret, webhook_url, webhook_secret

    async def resolve_media(
        self,
        access_secret: str,
        external_media_id: str,
    ) -> ChannelMediaReference:
        del access_secret
        return ChannelMediaReference(
            external_media_id=external_media_id,
            external_unique_id=external_media_id,
            file_path=None,
            size_bytes=None,
        )

    async def send_text(
        self,
        access_secret: str,
        *,
        external_thread_id: str,
        text: str,
    ) -> ChannelSendResult:
        self.send_calls.append((access_secret, external_thread_id, text))
        return ChannelSendResult(
            external_message_id=str(5000 + len(self.send_calls)),
            occurred_at=datetime.now(UTC),
        )


class FakeBusinessReferenceAdapter:
    kind = ToolAdapterKind.BUSINESS_REFERENCE

    def __init__(self, expected_secret: str) -> None:
        self.expected_secret = expected_secret
        self.calls: list[ToolAdapterRequest] = []

    async def execute(self, request: ToolAdapterRequest) -> dict[str, object]:
        self.calls.append(request)
        assert request.credential is not None
        assert request.credential.secret == self.expected_secret
        assert request.arguments == {"sku": "A-1"}
        return {"sku": "A-1", "stock": 7}


class RecordingStructuredAIProvider(MockAIProviderAdapter):
    def __init__(self) -> None:
        super().__init__(
            key="mock",
            structured_payloads=(
                {
                    "action": "tool_call",
                    "text": "",
                    "tool_name": "inventory.lookup@1",
                    "arguments": {"sku": "A-1"},
                },
                {
                    "action": "final",
                    "text": "A-1 is in stock with 7 units available.",
                    "tool_name": "",
                    "arguments": {},
                },
            ),
        )
        self.generation_requests: list[GenerationRequest] = []

    async def generate(
        self,
        model_id: str,
        request: GenerationRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> GenerationResult:
        self.generation_requests.append(request)
        return await super().generate(model_id, request, parameters, timeout_seconds)


@pytest.fixture(autouse=True)
def clean_runtime() -> Iterator[None]:
    with SYNC_ENGINE.begin() as connection:
        connection.execute(text("TRUNCATE TABLE platform_users, tenants CASCADE"))
    SYNC_REDIS.delete(CHANNEL_JOB_STREAM)
    yield
    SYNC_REDIS.delete(CHANNEL_JOB_STREAM)
    with SYNC_ENGINE.begin() as connection:
        connection.execute(text("TRUNCATE TABLE platform_users, tenants CASCADE"))


def seed_tenant_user(
    *,
    slug: str,
    name: str,
    email: str,
    password: str,
    role: TenantRole = TenantRole.OWNER,
    tenant_id: UUID | None = None,
) -> UUID:
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        if tenant_id is None:
            tenant = Tenant(slug=slug, name=name)
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
        return resolved_tenant_id


def login(client: TestClient, email: str, password: str) -> Response:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response


def open_client(adapter: FakeTelegramAdapter | None = None) -> TestClient:
    app = create_app(TEST_SETTINGS)
    client = TestClient(app)
    client.__enter__()
    if adapter is not None:
        app.state.channel_registry = ChannelRegistry((adapter,))
    return client


def close_client(client: TestClient) -> None:
    client.__exit__(None, None, None)


def object_schema(properties: dict[str, object], required: list[str]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def create_prompt(client: TestClient, tenant_id: UUID) -> UUID:
    created = client.post(
        f"/api/v1/tenants/{tenant_id}/prompts",
        json={"name": "Tool Prompt", "content": "Use approved live tools when needed."},
    )
    assert created.status_code == 201
    prompt_id = UUID(created.json()["id"])
    published = client.post(f"/api/v1/tenants/{tenant_id}/prompts/{prompt_id}/publish")
    assert published.status_code == 200
    return prompt_id


def create_agent(client: TestClient, tenant_id: UUID, prompt_id: UUID) -> UUID:
    created = client.post(
        f"/api/v1/tenants/{tenant_id}/agents",
        json={"name": "Tool Agent", "prompt_id": str(prompt_id)},
    )
    assert created.status_code == 201
    return UUID(created.json()["id"])


def tool_payload(*, risk_level: str = "low") -> dict[str, object]:
    return {
        "name": "inventory.lookup",
        "version": 1,
        "description": "Look up current inventory for a SKU.",
        "adapter_kind": "business_reference",
        "operation_type": "read",
        "risk_level": risk_level,
        "input_schema": object_schema({"sku": {"type": "string"}}, ["sku"]),
        "output_schema": object_schema(
            {"sku": {"type": "string"}, "stock": {"type": "integer"}},
            ["sku", "stock"],
        ),
        "configuration": {
            "url": "https://api.example.test/inventory",
            "method": "GET",
            "argument_location": "query",
        },
        "timeout_seconds": 5,
        "max_attempts": 2,
        "requires_approval": False,
    }


def create_tool(client: TestClient, tenant_id: UUID, *, risk_level: str = "low") -> UUID:
    response = client.post(f"/api/v1/tenants/{tenant_id}/tools", json=tool_payload(risk_level=risk_level))
    assert response.status_code == 201
    return UUID(response.json()["id"])


def test_tool_admin_api_rbac_tenant_secret_and_approval_foundation() -> None:
    tenant_a = seed_tenant_user(
        slug="tools-a",
        name="Tools A",
        email="owner-a@example.com",
        password="owner tools password a",
    )
    seed_tenant_user(
        slug="unused-a",
        name="unused",
        email="agent-a@example.com",
        password="agent tools password a",
        role=TenantRole.AGENT,
        tenant_id=tenant_a,
    )
    tenant_b = seed_tenant_user(
        slug="tools-b",
        name="Tools B",
        email="owner-b@example.com",
        password="owner tools password b",
    )

    client = open_client()
    secret = "BUSINESS_SECRET_MUST_NEVER_LEAK"
    try:
        login(client, "owner-a@example.com", "owner tools password a")
        prompt_id = create_prompt(client, tenant_a)
        agent_id = create_agent(client, tenant_a, prompt_id)
        tool_id = create_tool(client, tenant_a, risk_level="high")

        credential = client.put(
            f"/api/v1/tenants/{tenant_a}/tools/{tool_id}/credential",
            json={"auth_type": "bearer", "secret": secret},
        )
        assert credential.status_code == 200
        assert credential.json()["configured"] is True
        assert "secret" not in credential.json()

        tool = client.get(f"/api/v1/tenants/{tenant_a}/tools/{tool_id}")
        assert tool.status_code == 200
        assert tool.json()["credential"]["configured"] is True
        assert secret not in tool.text

        granted = client.put(f"/api/v1/tenants/{tenant_a}/agents/{agent_id}/tools/{tool_id}")
        assert granted.status_code == 200

        with Session(SYNC_ENGINE) as db:
            stored = db.scalar(
                select(ToolCredential).where(
                    ToolCredential.tenant_id == tenant_a,
                    ToolCredential.tool_id == tool_id,
                )
            )
            assert stored is not None
            assert secret.encode() not in stored.ciphertext
            assert len(stored.nonce) == 12

            approved_execution = ToolExecution(
                tenant_id=tenant_a,
                tool_id=tool_id,
                agent_id=agent_id,
                agent_run_id=None,
                call_ordinal=None,
                idempotency_key="approval-approved",
                status=ToolExecutionStatus.APPROVAL_REQUIRED.value,
                approval_status=ToolApprovalStatus.PENDING.value,
                input_payload={"sku": "A-1"},
            )
            denied_execution = ToolExecution(
                tenant_id=tenant_a,
                tool_id=tool_id,
                agent_id=agent_id,
                agent_run_id=None,
                call_ordinal=None,
                idempotency_key="approval-denied",
                status=ToolExecutionStatus.APPROVAL_REQUIRED.value,
                approval_status=ToolApprovalStatus.PENDING.value,
                input_payload={"sku": "A-2"},
            )
            db.add_all((approved_execution, denied_execution))
            db.commit()
            db.refresh(approved_execution)
            db.refresh(denied_execution)
            approved_id = approved_execution.id
            denied_id = denied_execution.id

        approved = client.put(
            f"/api/v1/tenants/{tenant_a}/tool-executions/{approved_id}/approval",
            json={"decision": "approved"},
        )
        assert approved.status_code == 200
        assert approved.json()["approval_status"] == "approved"
        assert approved.json()["status"] == "pending"

        replay = client.put(
            f"/api/v1/tenants/{tenant_a}/tool-executions/{approved_id}/approval",
            json={"decision": "approved"},
        )
        assert replay.status_code == 409

        denied = client.put(
            f"/api/v1/tenants/{tenant_a}/tool-executions/{denied_id}/approval",
            json={"decision": "denied"},
        )
        assert denied.status_code == 200
        assert denied.json()["approval_status"] == "denied"
        assert denied.json()["status"] == "denied"
        assert denied.json()["error_code"] == "tool_approval_denied"

        login(client, "agent-a@example.com", "agent tools password a")
        denied_create = client.post(
            f"/api/v1/tenants/{tenant_a}/tools",
            json={**tool_payload(), "name": "inventory.other"},
        )
        assert denied_create.status_code == 403
        denied_credential = client.put(
            f"/api/v1/tenants/{tenant_a}/tools/{tool_id}/credential",
            json={"auth_type": "bearer", "secret": "denied-secret"},
        )
        assert denied_credential.status_code == 403

        login(client, "owner-b@example.com", "owner tools password b")
        cross_tool = client.get(f"/api/v1/tenants/{tenant_b}/tools/{tool_id}")
        assert cross_tool.status_code == 404
        cross_execution = client.get(
            f"/api/v1/tenants/{tenant_b}/tool-executions/{approved_id}"
        )
        assert cross_execution.status_code == 404

        with Session(SYNC_ENGINE) as db:
            audit_repr = repr(
                [
                    item.details
                    for item in db.scalars(
                        select(AuditEvent).where(AuditEvent.tenant_id == tenant_a)
                    ).all()
                ]
            )
            assert secret not in audit_repr
            permission = db.scalar(
                select(AgentToolPermission).where(
                    AgentToolPermission.tenant_id == tenant_a,
                    AgentToolPermission.agent_id == agent_id,
                    AgentToolPermission.tool_id == tool_id,
                )
            )
            assert permission is not None
    finally:
        close_client(client)


def create_channel(client: TestClient, tenant_id: UUID) -> UUID:
    response = client.post(
        f"/api/v1/tenants/{tenant_id}/channels/telegram",
        json={
            "name": "Tool Bot",
            "bot_token": "800001:tool-test-token",
            "enabled_inbound_types": ["text"],
        },
    )
    assert response.status_code == 201
    return UUID(response.json()["id"])


def stored_webhook_secret(account_id: UUID) -> str:
    with Session(SYNC_ENGINE) as db:
        account = db.get(ChannelAccount, account_id)
        assert account is not None
        credential = db.scalar(
            select(ChannelCredential).where(
                ChannelCredential.channel_account_id == account_id,
                ChannelCredential.kind == ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET.value,
            )
        )
        assert credential is not None
        return decrypt_channel_secret(
            TEST_SETTINGS,
            account.tenant_id,
            account.id,
            ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET,
            ciphertext=credential.ciphertext,
            nonce=credential.nonce,
            key_version=credential.key_version,
        )


def seed_customer_response_profile(tenant_id: UUID) -> None:
    with Session(SYNC_ENGINE) as db:
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
                model_id="mock-tool-model",
                priority=0,
                parameters={},
            )
        )
        db.commit()


def post_webhook(
    client: TestClient,
    account_id: UUID,
    secret: str,
) -> Response:
    return client.post(
        f"/api/v1/webhooks/telegram/{account_id}",
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        json={
            "update_id": 88001,
            "message": {
                "message_id": 99001,
                "date": 1_700_800_001,
                "chat": {"id": 77, "type": "private"},
                "from": {
                    "id": 77,
                    "is_bot": False,
                    "first_name": "Ada",
                    "username": "ada",
                },
                "text": "Is SKU A-1 in stock?",
            },
        },
    )


def test_agent_tool_loop_executes_authorized_tool_once_without_secret_exposure() -> None:
    tenant_id = seed_tenant_user(
        slug="tool-e2e",
        name="Tool E2E",
        email="owner@example.com",
        password="owner tool e2e password",
    )
    channel_adapter = FakeTelegramAdapter()
    client = open_client(channel_adapter)
    business_secret = "SERVER_SIDE_TOOL_SECRET_123"
    try:
        login(client, "owner@example.com", "owner tool e2e password")
        channel_id = create_channel(client, tenant_id)
        prompt_id = create_prompt(client, tenant_id)
        agent_id = create_agent(client, tenant_id, prompt_id)
        assigned = client.put(
            f"/api/v1/tenants/{tenant_id}/agents/assignments/channels/{channel_id}",
            json={"agent_id": str(agent_id)},
        )
        assert assigned.status_code == 200
        seed_customer_response_profile(tenant_id)

        tool_id = create_tool(client, tenant_id)
        credential = client.put(
            f"/api/v1/tenants/{tenant_id}/tools/{tool_id}/credential",
            json={"auth_type": "bearer", "secret": business_secret},
        )
        assert credential.status_code == 200
        permission = client.put(
            f"/api/v1/tenants/{tenant_id}/agents/{agent_id}/tools/{tool_id}"
        )
        assert permission.status_code == 200

        webhook = post_webhook(client, channel_id, stored_webhook_secret(channel_id))
        assert webhook.status_code == 200

        ai_provider = RecordingStructuredAIProvider()
        business_adapter = FakeBusinessReferenceAdapter(business_secret)

        async def run() -> UUID:
            engine, session_factory = create_database(TEST_SETTINGS)
            redis_client = create_channel_redis(TEST_SETTINGS)
            queue = ChannelJobQueue(redis_client)
            try:
                await queue.ensure_group()
                jobs = await queue.consume("m8-tool-consumer", block_ms=20)
                assert len(jobs) == 1
                tool_runtime = ToolRuntime(
                    TEST_SETTINGS,
                    session_factory,
                    ToolAdapterRegistry((business_adapter,)),
                )
                gateway = AIGateway(AIProviderRegistry((ai_provider,)), session_factory)
                await process_job(
                    jobs[0],
                    queue,
                    ChannelRegistry((channel_adapter,)),
                    gateway,
                    TEST_SETTINGS,
                    session_factory,
                    tool_runtime,
                )
                await process_agent_event(
                    session_factory,
                    gateway,
                    ChannelRegistry((channel_adapter,)),
                    TEST_SETTINGS,
                    jobs[0].event_id,
                    tool_runtime=tool_runtime,
                )
                return jobs[0].event_id
            finally:
                await redis_client.aclose()
                await engine.dispose()

        event_id = asyncio.run(run())

        assert len(ai_provider.generation_requests) == 2
        assert len(business_adapter.calls) == 1
        assert len(channel_adapter.send_calls) == 1
        assert channel_adapter.send_calls[0][2] == "A-1 is in stock with 7 units available."
        all_model_text = "\n".join(
            (request.instructions or "") + "\n" + request.input_text
            for request in ai_provider.generation_requests
        )
        assert business_secret not in all_model_text
        assert "inventory.lookup@1" in (ai_provider.generation_requests[0].instructions or "")
        assert '"stock":7' in ai_provider.generation_requests[1].input_text

        with Session(SYNC_ENGINE) as db:
            event = db.get(ChannelInboundEvent, event_id)
            assert event is not None
            run_row = db.scalar(select(AgentRun).where(AgentRun.inbound_message_id == event.message_id))
            assert run_row is not None
            executions = list(
                db.scalars(
                    select(ToolExecution).where(ToolExecution.agent_run_id == run_row.id)
                ).all()
            )
            assert len(executions) == 1
            assert executions[0].status == ToolExecutionStatus.SUCCEEDED.value
            assert executions[0].attempt_count == 1
            assert executions[0].output_payload == {"sku": "A-1", "stock": 7}
            outbound = list(
                db.scalars(
                    select(Message).where(
                        Message.tenant_id == tenant_id,
                        Message.conversation_id == run_row.conversation_id,
                    )
                ).all()
            )
            assert any(message.text == "A-1 is in stock with 7 units available." for message in outbound)

        async def unauthorized() -> None:
            engine, session_factory = create_database(TEST_SETTINGS)
            runtime = ToolRuntime(TEST_SETTINGS, session_factory, ToolAdapterRegistry(()))
            try:
                with pytest.raises(ToolRuntimeError, match="tool_not_authorized"):
                    await runtime.execute_agent_tool(
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        agent_run_id=uuid4(),
                        call_ordinal=2,
                        qualified_name="inventory.unapproved@1",
                        arguments={"sku": "A-1"},
                    )
            finally:
                await engine.dispose()

        asyncio.run(unauthorized())
    finally:
        close_client(client)
