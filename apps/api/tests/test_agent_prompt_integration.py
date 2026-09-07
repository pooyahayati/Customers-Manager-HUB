import asyncio
import base64
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import SecretStr
from redis import Redis
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from customers_manager_hub.agent_models import (
    AgentPromptVersion,
    AgentRun,
    AgentRunStatus,
    PromptVersionStatus,
)
from customers_manager_hub.agent_runtime import (
    build_conversation_input,
    process_agent_event,
)
from customers_manager_hub.ai_gateway import (
    AIGateway,
    AIProviderRegistry,
    GenerationRequest,
    GenerationResult,
    MockAIProviderAdapter,
)
from customers_manager_hub.ai_models import AIExecutionTrace, AITaskProfile, AITaskRoute, AITaskType
from customers_manager_hub.channel_gateway import (
    ChannelAccountIdentity,
    ChannelMediaReference,
    ChannelProviderError,
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
    MessageAuthorType,
    MessageDirection,
    PlatformUser,
    Tenant,
    TenantMembership,
    TenantRole,
)
from customers_manager_hub.security import hash_password
from customers_manager_hub.worker import process_job

RUN_DB_INTEGRATION = os.environ.get("RUN_DB_INTEGRATION") == "1"
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://cmh:change-me@localhost:5432/customers_manager_hub",
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
TEST_ENCRYPTION_KEY = base64.urlsafe_b64encode(b"a" * 32).decode()
TEST_SETTINGS = Settings(
    app_env="test",
    database_url=DATABASE_URL,
    redis_url=REDIS_URL,
    encryption_key=SecretStr(TEST_ENCRYPTION_KEY),
    telegram_webhook_base_url="https://agents.example.test",
)
SYNC_ENGINE = create_engine(TEST_SETTINGS.sqlalchemy_database_url)
SYNC_REDIS = Redis.from_url(REDIS_URL, decode_responses=True)  # pyright: ignore[reportUnknownMemberType]

pytestmark = pytest.mark.skipif(
    not RUN_DB_INTEGRATION,
    reason="Agent integration tests require RUN_DB_INTEGRATION=1",
)


class FakeTelegramAdapter:
    channel_type = ChannelType.TELEGRAM
    capabilities = frozenset(
        {
            ChannelCapability.TEXT,
            ChannelCapability.VOICE,
            ChannelCapability.OUTBOUND_TEXT,
        }
    )

    def __init__(self) -> None:
        self.send_attempts = 0
        self.send_calls: list[tuple[str, str, str]] = []
        self.fail_next_send = False

    async def validate_account(self, access_secret: str) -> ChannelAccountIdentity:
        assert access_secret == "700001:agent-test-token"
        return ChannelAccountIdentity(
            external_account_id="700001",
            username="agent_test_bot",
            display_name="Agent Test Bot",
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
        self.send_attempts += 1
        self.send_calls.append((access_secret, external_thread_id, text))
        if self.fail_next_send:
            self.fail_next_send = False
            raise ChannelProviderError("telegram_test_retryable", retryable=True)
        return ChannelSendResult(
            external_message_id=str(1000 + self.send_attempts),
            occurred_at=datetime.now(UTC),
        )


class RecordingMockAIProvider(MockAIProviderAdapter):
    def __init__(self, *, generation_text: str, failures_before_success: int = 0) -> None:
        super().__init__(
            key="mock",
            generation_text=generation_text,
            failures_before_success=failures_before_success,
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


class LeaseObservingAIProvider(RecordingMockAIProvider):
    def __init__(self, *, generation_text: str) -> None:
        super().__init__(generation_text=generation_text)
        self.lease_samples: list[datetime] = []

    async def generate(
        self,
        model_id: str,
        request: GenerationRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> GenerationResult:
        with Session(SYNC_ENGINE) as db:
            first = db.scalar(select(AgentRun.lease_until))
            assert first is not None
            self.lease_samples.append(first)
        await asyncio.sleep(0.05)
        with Session(SYNC_ENGINE) as db:
            second = db.scalar(select(AgentRun.lease_until))
            assert second is not None
            self.lease_samples.append(second)
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


def open_client(adapter: FakeTelegramAdapter) -> tuple[object, TestClient]:
    app = create_app(TEST_SETTINGS)
    client = TestClient(app)
    client.__enter__()
    app.state.channel_registry = ChannelRegistry((adapter,))
    return app, client


def close_client(client: TestClient) -> None:
    client.__exit__(None, None, None)


def create_channel(client: TestClient, tenant_id: UUID) -> UUID:
    response = client.post(
        f"/api/v1/tenants/{tenant_id}/channels/telegram",
        json={
            "name": "Agent Bot",
            "bot_token": "700001:agent-test-token",
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


def create_published_prompt(client: TestClient, tenant_id: UUID, content: str) -> tuple[UUID, UUID]:
    created = client.post(
        f"/api/v1/tenants/{tenant_id}/prompts",
        json={"name": "Customer Prompt", "content": content},
    )
    assert created.status_code == 201
    prompt_id = UUID(created.json()["id"])
    published = client.post(f"/api/v1/tenants/{tenant_id}/prompts/{prompt_id}/publish")
    assert published.status_code == 200
    published_versions = [
        version for version in published.json()["versions"] if version["status"] == "published"
    ]
    assert len(published_versions) == 1
    return prompt_id, UUID(published_versions[0]["id"])


def create_agent_and_assign(
    client: TestClient,
    tenant_id: UUID,
    prompt_id: UUID,
    channel_account_id: UUID,
) -> UUID:
    created = client.post(
        f"/api/v1/tenants/{tenant_id}/agents",
        json={"name": "Sales Agent", "prompt_id": str(prompt_id)},
    )
    assert created.status_code == 201
    agent_id = UUID(created.json()["id"])
    assigned = client.put(
        f"/api/v1/tenants/{tenant_id}/agents/assignments/channels/{channel_account_id}",
        json={"agent_id": str(agent_id)},
    )
    assert assigned.status_code == 200
    return agent_id


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
                model_id="mock-customer-model",
                priority=0,
                parameters={},
            )
        )
        db.commit()


def telegram_update(update_id: int, message_id: int, text_value: str) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": 1_700_100_000 + message_id,
            "chat": {"id": 55, "type": "private"},
            "from": {
                "id": 55,
                "is_bot": False,
                "first_name": "Grace",
                "username": "grace",
            },
            "text": text_value,
        },
    }


def post_webhook(
    client: TestClient,
    account_id: UUID,
    secret: str,
    *,
    update_id: int,
    message_id: int,
    text_value: str,
) -> Response:
    return client.post(
        f"/api/v1/webhooks/telegram/{account_id}",
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        json=telegram_update(update_id, message_id, text_value),
    )


def test_prompt_lifecycle_rbac_and_tenant_isolation() -> None:
    tenant_a = seed_tenant_user(
        slug="agent-a",
        name="Agent A",
        email="owner-a@example.com",
        password="owner password a",
    )
    for role, email in (
        (TenantRole.ADMIN, "admin-a@example.com"),
        (TenantRole.SUPERVISOR, "supervisor-a@example.com"),
        (TenantRole.AGENT, "agent-a@example.com"),
        (TenantRole.ANALYST, "analyst-a@example.com"),
        (TenantRole.VIEWER, "viewer-a@example.com"),
    ):
        seed_tenant_user(
            slug="unused",
            name="unused",
            email=email,
            password=f"{role.value} password a",
            role=role,
            tenant_id=tenant_a,
        )
    tenant_b = seed_tenant_user(
        slug="agent-b",
        name="Agent B",
        email="owner-b@example.com",
        password="owner password b",
    )

    adapter = FakeTelegramAdapter()
    _, client = open_client(adapter)
    try:
        login(client, "owner-a@example.com", "owner password a")
        channel_id = create_channel(client, tenant_a)
        created = client.post(
            f"/api/v1/tenants/{tenant_a}/prompts",
            json={
                "name": "Versioned Prompt",
                "content": "PROMPT_CONTENT_MUST_NOT_ENTER_AUDIT",
            },
        )
        assert created.status_code == 201
        prompt_id = UUID(created.json()["id"])
        assert [(item["version"], item["status"]) for item in created.json()["versions"]] == [
            (1, "draft")
        ]

        published_v1 = client.post(f"/api/v1/tenants/{tenant_a}/prompts/{prompt_id}/publish")
        assert published_v1.status_code == 200
        assert [(item["version"], item["status"]) for item in published_v1.json()["versions"]] == [
            (1, "published")
        ]

        draft_v2 = client.put(
            f"/api/v1/tenants/{tenant_a}/prompts/{prompt_id}/draft",
            json={"content": "Second published behavior"},
        )
        assert draft_v2.status_code == 200
        assert [(item["version"], item["status"]) for item in draft_v2.json()["versions"]] == [
            (1, "published"),
            (2, "draft"),
        ]

        published_v2 = client.post(f"/api/v1/tenants/{tenant_a}/prompts/{prompt_id}/publish")
        assert published_v2.status_code == 200
        assert [(item["version"], item["status"]) for item in published_v2.json()["versions"]] == [
            (1, "archived"),
            (2, "published"),
        ]

        agent = client.post(
            f"/api/v1/tenants/{tenant_a}/agents",
            json={"name": "Configured Agent", "prompt_id": str(prompt_id)},
        )
        assert agent.status_code == 201
        agent_id = UUID(agent.json()["id"])
        assigned = client.put(
            f"/api/v1/tenants/{tenant_a}/agents/assignments/channels/{channel_id}",
            json={"agent_id": str(agent_id)},
        )
        assert assigned.status_code == 200

        login(client, "admin-a@example.com", "admin password a")
        admin_update = client.patch(
            f"/api/v1/tenants/{tenant_a}/agents/{agent_id}",
            json={"description": "Admin can update"},
        )
        assert admin_update.status_code == 200

        for role, email in (
            (TenantRole.SUPERVISOR, "supervisor-a@example.com"),
            (TenantRole.AGENT, "agent-a@example.com"),
            (TenantRole.ANALYST, "analyst-a@example.com"),
            (TenantRole.VIEWER, "viewer-a@example.com"),
        ):
            login(client, email, f"{role.value} password a")
            denied = client.post(
                f"/api/v1/tenants/{tenant_a}/prompts",
                json={"name": "Denied", "content": "Denied"},
            )
            assert denied.status_code == 403

        login(client, "owner-b@example.com", "owner password b")
        cross_prompt = client.get(f"/api/v1/tenants/{tenant_b}/prompts/{prompt_id}")
        assert cross_prompt.status_code == 404
        cross_agent = client.get(f"/api/v1/tenants/{tenant_b}/agents/{agent_id}")
        assert cross_agent.status_code == 404
        cross_assignment = client.get(
            f"/api/v1/tenants/{tenant_b}/agents/assignments/channels/{channel_id}"
        )
        assert cross_assignment.status_code == 404

        with Session(SYNC_ENGINE) as db:
            versions = list(
                db.scalars(
                    select(AgentPromptVersion)
                    .where(AgentPromptVersion.prompt_id == prompt_id)
                    .order_by(AgentPromptVersion.version)
                ).all()
            )
            assert [version.status for version in versions] == ["archived", "published"]
            duplicate_draft = AgentPromptVersion(
                tenant_id=tenant_a,
                prompt_id=prompt_id,
                version=3,
                status=PromptVersionStatus.DRAFT.value,
                content="draft three",
            )
            db.add(duplicate_draft)
            db.commit()
            db.add(
                AgentPromptVersion(
                    tenant_id=tenant_a,
                    prompt_id=prompt_id,
                    version=4,
                    status=PromptVersionStatus.DRAFT.value,
                    content="illegal second draft",
                )
            )
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()

            audit_details = repr(
                [
                    audit.details
                    for audit in db.scalars(
                        select(AuditEvent).where(AuditEvent.tenant_id == tenant_a)
                    ).all()
                ]
            )
            assert "PROMPT_CONTENT_MUST_NOT_ENTER_AUDIT" not in audit_details
    finally:
        close_client(client)


async def process_one_job(
    *,
    adapter: FakeTelegramAdapter,
    ai_provider: RecordingMockAIProvider,
    consumer: str,
    reclaim: bool,
) -> None:
    engine, session_factory = create_database(TEST_SETTINGS)
    redis_client = create_channel_redis(TEST_SETTINGS)
    queue = ChannelJobQueue(redis_client, claim_idle_ms=0)
    try:
        await queue.ensure_group()
        jobs = (
            await queue.reclaim(consumer) if reclaim else await queue.consume(consumer, block_ms=1)
        )
        assert len(jobs) == 1
        await process_job(
            jobs[0],
            queue,
            ChannelRegistry((adapter,)),
            AIGateway(AIProviderRegistry((ai_provider,)), session_factory),
            TEST_SETTINGS,
            session_factory,
        )
    finally:
        await redis_client.aclose()
        await engine.dispose()


def configure_runtime(
    client: TestClient,
    adapter: FakeTelegramAdapter,
    *,
    prompt_content: str,
) -> tuple[UUID, UUID, UUID, str]:
    del adapter
    tenant_id = seed_tenant_user(
        slug="agent-runtime",
        name="Agent Runtime",
        email="owner@example.com",
        password="owner runtime password",
    )
    login(client, "owner@example.com", "owner runtime password")
    channel_id = create_channel(client, tenant_id)
    prompt_id, prompt_version_id = create_published_prompt(client, tenant_id, prompt_content)
    create_agent_and_assign(client, tenant_id, prompt_id, channel_id)
    seed_customer_response_profile(tenant_id)
    return tenant_id, channel_id, prompt_version_id, stored_webhook_secret(channel_id)


def test_worker_generates_resumes_dispatch_and_deduplicates_agent_response() -> None:
    adapter = FakeTelegramAdapter()
    _, client = open_client(adapter)
    try:
        tenant_id, channel_id, prompt_version_id, webhook_secret = configure_runtime(
            client,
            adapter,
            prompt_content="Answer in Persian and never invent order status.",
        )
        accepted = post_webhook(
            client,
            channel_id,
            webhook_secret,
            update_id=501,
            message_id=51,
            text_value="Where is my order?",
        )
        assert accepted.status_code == 200

        ai_provider = RecordingMockAIProvider(generation_text="Your order is being checked.")
        adapter.fail_next_send = True
        asyncio.run(
            process_one_job(
                adapter=adapter,
                ai_provider=ai_provider,
                consumer="agent-consumer-a",
                reclaim=False,
            )
        )
        assert ai_provider.calls == 1
        assert adapter.send_attempts == 1
        assert SYNC_REDIS.xlen(CHANNEL_JOB_STREAM) == 1

        with Session(SYNC_ENGINE) as db:
            run = db.scalar(select(AgentRun).where(AgentRun.tenant_id == tenant_id))
            assert run is not None
            run_id = run.id
            assert run.status == AgentRunStatus.GENERATED.value
            assert run.generated_text == "Your order is being checked."
            assert run.prompt_version_id == prompt_version_id
            event = db.scalar(
                select(ChannelInboundEvent).where(
                    ChannelInboundEvent.channel_account_id == channel_id,
                    ChannelInboundEvent.external_event_id == "501",
                )
            )
            assert event is not None
            event_id = event.id

        asyncio.run(
            process_one_job(
                adapter=adapter,
                ai_provider=ai_provider,
                consumer="agent-consumer-b",
                reclaim=True,
            )
        )
        assert ai_provider.calls == 1
        assert adapter.send_attempts == 2
        assert adapter.send_calls[-1][2] == "Your order is being checked."
        assert SYNC_REDIS.xlen(CHANNEL_JOB_STREAM) == 0

        with Session(SYNC_ENGINE) as db:
            run = db.get(AgentRun, run_id)
            assert run is not None
            assert run.status == AgentRunStatus.SUCCEEDED.value
            assert run.generated_text is None
            assert run.outbound_message_id is not None
            outbound = db.get(Message, run.outbound_message_id)
            assert outbound is not None
            assert outbound.direction == MessageDirection.OUTBOUND.value
            assert outbound.author_type == MessageAuthorType.AI.value
            assert outbound.text == "Your order is being checked."
            assert outbound.idempotency_key == f"agent:{run.id}"
            assert db.scalar(select(func.count()).select_from(AIExecutionTrace)) == 1

        assert len(ai_provider.generation_requests) == 1
        request = ai_provider.generation_requests[0]
        assert request.instructions is not None
        assert "Answer in Persian and never invent order status." in request.instructions
        assert "Where is my order?" not in request.instructions
        assert "Where is my order?" in request.input_text

        async def requeue_and_process() -> None:
            engine, session_factory = create_database(TEST_SETTINGS)
            redis_client = create_channel_redis(TEST_SETTINGS)
            queue = ChannelJobQueue(redis_client, claim_idle_ms=0)
            try:
                await queue.enqueue(event_id)
                jobs = await queue.consume("agent-consumer-c", block_ms=1)
                assert len(jobs) == 1
                await process_job(
                    jobs[0],
                    queue,
                    ChannelRegistry((adapter,)),
                    AIGateway(AIProviderRegistry((ai_provider,)), session_factory),
                    TEST_SETTINGS,
                    session_factory,
                )
            finally:
                await redis_client.aclose()
                await engine.dispose()

        asyncio.run(requeue_and_process())
        assert ai_provider.calls == 1
        assert adapter.send_attempts == 2
        assert SYNC_REDIS.xlen(CHANNEL_JOB_STREAM) == 0
    finally:
        close_client(client)


def test_long_generation_renews_lease_and_context_budget_is_enforced() -> None:
    adapter = FakeTelegramAdapter()
    _, client = open_client(adapter)
    try:
        tenant_id, channel_id, _, webhook_secret = configure_runtime(
            client,
            adapter,
            prompt_content="Answer briefly.",
        )
        accepted = post_webhook(
            client,
            channel_id,
            webhook_secret,
            update_id=551,
            message_id=56,
            text_value="Please help with my order",
        )
        assert accepted.status_code == 200
        with Session(SYNC_ENGINE) as db:
            event = db.scalar(
                select(ChannelInboundEvent).where(
                    ChannelInboundEvent.channel_account_id == channel_id,
                    ChannelInboundEvent.external_event_id == "551",
                )
            )
            assert event is not None
            event_id = event.id

        ai_provider = LeaseObservingAIProvider(generation_text="I can help with that.")

        async def run_agent() -> None:
            engine, session_factory = create_database(TEST_SETTINGS)
            try:
                await process_agent_event(
                    session_factory,
                    AIGateway(AIProviderRegistry((ai_provider,)), session_factory),
                    ChannelRegistry((adapter,)),
                    TEST_SETTINGS,
                    event_id,
                    lease_renew_interval_seconds=0.01,
                )
            finally:
                await engine.dispose()

        asyncio.run(run_agent())
        assert len(ai_provider.lease_samples) == 2
        assert ai_provider.lease_samples[1] > ai_provider.lease_samples[0]
        assert ai_provider.calls == 1
        assert adapter.send_attempts == 1

        with Session(SYNC_ENGINE) as db:
            run = db.scalar(select(AgentRun).where(AgentRun.tenant_id == tenant_id))
            assert run is not None
            assert run.status == AgentRunStatus.SUCCEEDED.value
            conversation_id = run.conversation_id
            inbound_message_id = run.inbound_message_id

        async def bounded_context() -> str:
            engine, session_factory = create_database(TEST_SETTINGS)
            try:
                return await build_conversation_input(
                    session_factory,
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    current_message_id=inbound_message_id,
                    current_text="Please help with my order",
                    history_char_budget=10,
                )
            finally:
                await engine.dispose()

        model_input = asyncio.run(bounded_context())
        history_section, current_section = model_input.split(
            "\n\nCurrent customer message:\n",
            1,
        )
        history_payload = history_section.split("\n", 1)[1]
        assert len(history_payload) <= 10
        assert current_section == "CUSTOMER: Please help with my order"
    finally:
        close_client(client)


def test_retryable_ai_failure_is_reclaimed_and_terminal_output_failure_is_acked() -> None:
    adapter = FakeTelegramAdapter()
    _, client = open_client(adapter)
    try:
        tenant_id, channel_id, _, webhook_secret = configure_runtime(
            client,
            adapter,
            prompt_content="Give a short factual answer.",
        )
        accepted = post_webhook(
            client,
            channel_id,
            webhook_secret,
            update_id=601,
            message_id=61,
            text_value="Hello",
        )
        assert accepted.status_code == 200

        retrying_ai = RecordingMockAIProvider(
            generation_text="Hello back",
            failures_before_success=1,
        )
        asyncio.run(
            process_one_job(
                adapter=adapter,
                ai_provider=retrying_ai,
                consumer="ai-retry-a",
                reclaim=False,
            )
        )
        assert retrying_ai.calls == 1
        assert adapter.send_attempts == 0
        assert SYNC_REDIS.xlen(CHANNEL_JOB_STREAM) == 1
        with Session(SYNC_ENGINE) as db:
            run = db.scalar(select(AgentRun).where(AgentRun.tenant_id == tenant_id))
            assert run is not None
            assert run.status == AgentRunStatus.PENDING.value
            assert run.error_code == "mock_retryable_failure"

        asyncio.run(
            process_one_job(
                adapter=adapter,
                ai_provider=retrying_ai,
                consumer="ai-retry-b",
                reclaim=True,
            )
        )
        assert retrying_ai.calls == 2
        assert adapter.send_attempts == 1
        assert SYNC_REDIS.xlen(CHANNEL_JOB_STREAM) == 0

        with SYNC_ENGINE.begin() as connection:
            connection.execute(text("TRUNCATE TABLE agent_runs, ai_execution_traces CASCADE"))
        accepted_invalid = post_webhook(
            client,
            channel_id,
            webhook_secret,
            update_id=602,
            message_id=62,
            text_value="Another question",
        )
        assert accepted_invalid.status_code == 200
        blank_ai = RecordingMockAIProvider(generation_text="   ")
        asyncio.run(
            process_one_job(
                adapter=adapter,
                ai_provider=blank_ai,
                consumer="ai-terminal",
                reclaim=False,
            )
        )
        assert blank_ai.calls == 1
        assert adapter.send_attempts == 1
        assert SYNC_REDIS.xlen(CHANNEL_JOB_STREAM) == 0
        with Session(SYNC_ENGINE) as db:
            runs = list(db.scalars(select(AgentRun)).all())
            assert len(runs) == 1
            assert runs[0].status == AgentRunStatus.FAILED.value
            assert runs[0].error_code == "agent_empty_output"
            assert runs[0].generated_text is None
    finally:
        close_client(client)
