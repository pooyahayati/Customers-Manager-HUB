import asyncio
import base64
import hashlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from openpyxl import Workbook
from pydantic import SecretStr
from redis import Redis
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from customers_manager_hub.agent_models import AgentRun
from customers_manager_hub.ai_gateway import (
    AIGateway,
    AIProviderRegistry,
    AIUsage,
    EmbeddingRequest,
    EmbeddingResult,
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
from customers_manager_hub.knowledge_models import (
    AgentKnowledgePermission,
    KnowledgeChunk,
    KnowledgeRetrievalTrace,
    KnowledgeSource,
    KnowledgeSourceStatus,
)
from customers_manager_hub.knowledge_queue import KNOWLEDGE_JOB_STREAM, KnowledgeJobQueue
from customers_manager_hub.knowledge_runtime import (
    KnowledgeRuntime,
    KnowledgeRuntimeError,
    process_knowledge_source,
)
from customers_manager_hub.knowledge_storage import (
    XLSX_MEDIA_TYPE,
    InMemoryObjectStorage,
    KnowledgeStorageError,
)
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
    encryption_key=SecretStr(base64.urlsafe_b64encode(b"k" * 32).decode()),
    telegram_webhook_base_url="https://knowledge.example.test",
)
SYNC_ENGINE = create_engine(TEST_SETTINGS.sqlalchemy_database_url)
SYNC_REDIS = Redis.from_url(REDIS_URL, decode_responses=True)  # pyright: ignore[reportUnknownMemberType]

pytestmark = pytest.mark.skipif(
    not RUN_DB_INTEGRATION,
    reason="Knowledge integration tests require RUN_DB_INTEGRATION=1",
)


class FakeTelegramAdapter:
    channel_type = ChannelType.TELEGRAM
    capabilities = frozenset({ChannelCapability.TEXT, ChannelCapability.OUTBOUND_TEXT})

    def __init__(self) -> None:
        self.send_calls: list[tuple[str, str, str]] = []

    async def validate_account(self, access_secret: str) -> ChannelAccountIdentity:
        assert access_secret == "900001:knowledge-test-token"
        return ChannelAccountIdentity(
            external_account_id="900001",
            username="knowledge_test_bot",
            display_name="Knowledge Test Bot",
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
            external_message_id=str(7000 + len(self.send_calls)),
            occurred_at=datetime.now(UTC),
        )


class KnowledgeAIProvider(MockAIProviderAdapter):
    def __init__(self) -> None:
        super().__init__(key="mock", generation_text="Returns are accepted within 30 days [K1].")
        self.generation_requests: list[GenerationRequest] = []
        self.embedding_requests: list[EmbeddingRequest] = []

    async def generate(
        self,
        model_id: str,
        request: GenerationRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> GenerationResult:
        del parameters, timeout_seconds
        self.generation_requests.append(request)
        self.calls += 1
        return GenerationResult(
            provider=self.key,
            model_id=model_id,
            text="Returns are accepted within 30 days [K1].",
            structured=None,
            usage=AIUsage(input_tokens=10, output_tokens=8, total_tokens=18),
            provider_request_id=f"knowledge-generation-{self.calls}",
        )

    async def embed(
        self,
        model_id: str,
        request: EmbeddingRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> EmbeddingResult:
        del parameters, timeout_seconds
        self.embedding_requests.append(request)
        embeddings: list[tuple[float, ...]] = []
        for value in request.inputs:
            normalized = value.casefold()
            if any(term in normalized for term in ("refund", "return", "30 days")):
                embeddings.append((1.0, 0.05, 0.0))
            else:
                embeddings.append((0.05, 1.0, 0.0))
        self.calls += 1
        return EmbeddingResult(
            provider=self.key,
            model_id=model_id,
            embeddings=tuple(embeddings),
            usage=AIUsage(input_tokens=len(request.inputs), total_tokens=len(request.inputs)),
            provider_request_id=f"knowledge-embedding-{self.calls}",
        )


class FlakyReadStorage(InMemoryObjectStorage):
    def __init__(self) -> None:
        super().__init__()
        self.failures_remaining = 1

    async def get_bytes(self, key: str) -> bytes:
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise KnowledgeStorageError("knowledge_storage_transport_error", retryable=True)
        return await super().get_bytes(key)


@pytest.fixture(autouse=True)
def clean_runtime() -> Iterator[None]:
    with SYNC_ENGINE.begin() as connection:
        connection.execute(text("TRUNCATE TABLE platform_users, tenants CASCADE"))
    SYNC_REDIS.delete(CHANNEL_JOB_STREAM, KNOWLEDGE_JOB_STREAM)
    yield
    SYNC_REDIS.delete(CHANNEL_JOB_STREAM, KNOWLEDGE_JOB_STREAM)
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


def open_client(
    storage: InMemoryObjectStorage,
    adapter: FakeTelegramAdapter | None = None,
) -> TestClient:
    app = create_app(TEST_SETTINGS)
    client = TestClient(app)
    client.__enter__()
    app.state.knowledge_storage = storage
    if adapter is not None:
        app.state.channel_registry = ChannelRegistry((adapter,))
    return client


def close_client(client: TestClient) -> None:
    client.__exit__(None, None, None)


def _xlsx_bytes(text_value: str = "Returns are accepted within 30 days.") -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = "Returns"
    worksheet.append(["Policy", text_value])
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def create_prompt(client: TestClient, tenant_id: UUID, *, name: str = "Knowledge Prompt") -> UUID:
    created = client.post(
        f"/api/v1/tenants/{tenant_id}/prompts",
        json={"name": name, "content": "Use retrieved policy evidence and cite it when relevant."},
    )
    assert created.status_code == 201
    prompt_id = UUID(created.json()["id"])
    published = client.post(f"/api/v1/tenants/{tenant_id}/prompts/{prompt_id}/publish")
    assert published.status_code == 200
    return prompt_id


def create_agent(
    client: TestClient,
    tenant_id: UUID,
    prompt_id: UUID,
    *,
    name: str,
) -> UUID:
    created = client.post(
        f"/api/v1/tenants/{tenant_id}/agents",
        json={"name": name, "prompt_id": str(prompt_id)},
    )
    assert created.status_code == 201
    return UUID(created.json()["id"])


def seed_ai_profiles(tenant_id: UUID) -> None:
    with Session(SYNC_ENGINE) as db:
        for task_type, model_id in (
            (AITaskType.EMBEDDING, "mock-embedding"),
            (AITaskType.CUSTOMER_RESPONSE, "mock-customer-response"),
        ):
            profile = AITaskProfile(
                tenant_id=tenant_id,
                task_type=task_type.value,
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
                    model_id=model_id,
                    priority=0,
                    parameters={},
                )
            )
        db.commit()


def create_channel(client: TestClient, tenant_id: UUID) -> UUID:
    response = client.post(
        f"/api/v1/tenants/{tenant_id}/channels/telegram",
        json={
            "name": "Knowledge Bot",
            "bot_token": "900001:knowledge-test-token",
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


def post_webhook(client: TestClient, account_id: UUID, secret: str) -> Response:
    return client.post(
        f"/api/v1/webhooks/telegram/{account_id}",
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        json={
            "update_id": 99001,
            "message": {
                "message_id": 99002,
                "date": 1_700_900_001,
                "chat": {"id": 88, "type": "private"},
                "from": {
                    "id": 88,
                    "is_bot": False,
                    "first_name": "RAG",
                    "username": "rag-user",
                },
                "text": "What is your refund policy?",
            },
        },
    )


def test_knowledge_api_ingestion_retrieval_tenant_isolation_and_agent_rag() -> None:
    tenant_a = seed_tenant_user(
        slug="knowledge-a",
        name="Knowledge A",
        email="owner-a@example.com",
        password="owner knowledge password a",
    )
    seed_tenant_user(
        slug="unused-knowledge-a",
        name="unused",
        email="agent-a@example.com",
        password="agent knowledge password a",
        role=TenantRole.AGENT,
        tenant_id=tenant_a,
    )
    tenant_b = seed_tenant_user(
        slug="knowledge-b",
        name="Knowledge B",
        email="owner-b@example.com",
        password="owner knowledge password b",
    )
    storage = InMemoryObjectStorage()
    channel_adapter = FakeTelegramAdapter()
    client = open_client(storage, channel_adapter)
    ai_provider = KnowledgeAIProvider()
    try:
        login(client, "owner-a@example.com", "owner knowledge password a")
        base_response = client.post(
            f"/api/v1/tenants/{tenant_a}/knowledge-bases",
            json={"name": "Policies", "description": "Authoritative customer policies"},
        )
        assert base_response.status_code == 201
        base_id = UUID(base_response.json()["id"])

        prompt_id = create_prompt(client, tenant_a)
        agent_id = create_agent(client, tenant_a, prompt_id, name="Knowledge Agent")
        unassigned_agent_id = create_agent(
            client,
            tenant_a,
            prompt_id,
            name="Unassigned Knowledge Agent",
        )
        granted = client.put(
            f"/api/v1/tenants/{tenant_a}/agents/{agent_id}/knowledge-bases/{base_id}"
        )
        assert granted.status_code == 200
        seed_ai_profiles(tenant_a)

        upload = client.post(
            f"/api/v1/tenants/{tenant_a}/knowledge-bases/{base_id}/sources",
            files={
                "file": (
                    "../../policy.xlsx",
                    _xlsx_bytes(),
                    XLSX_MEDIA_TYPE,
                )
            },
        )
        assert upload.status_code == 202
        source_id = UUID(upload.json()["id"])
        assert upload.json()["filename"] == "policy.xlsx"
        assert upload.json()["status"] == "queued"
        assert len(storage.objects) == 1
        object_key = next(iter(storage.objects))
        assert object_key == f"knowledge/{tenant_a}/{base_id}/{source_id}.xlsx"
        assert "policy.xlsx" not in object_key

        login(client, "agent-a@example.com", "agent knowledge password a")
        denied_upload = client.post(
            f"/api/v1/tenants/{tenant_a}/knowledge-bases/{base_id}/sources",
            files={"file": ("denied.xlsx", _xlsx_bytes("Other"), XLSX_MEDIA_TYPE)},
        )
        assert denied_upload.status_code == 403

        login(client, "owner-b@example.com", "owner knowledge password b")
        assert (
            client.get(f"/api/v1/tenants/{tenant_b}/knowledge-bases/{base_id}").status_code == 404
        )
        assert (
            client.get(f"/api/v1/tenants/{tenant_b}/knowledge-sources/{source_id}").status_code
            == 404
        )

        async def ingest_and_retrieve() -> tuple[str, UUID]:
            engine, session_factory = create_database(TEST_SETTINGS)
            redis_client = create_channel_redis(TEST_SETTINGS)
            queue = KnowledgeJobQueue(redis_client)
            gateway = AIGateway(AIProviderRegistry((ai_provider,)), session_factory)
            runtime = KnowledgeRuntime(session_factory, gateway)
            try:
                await queue.ensure_group()
                jobs = await queue.consume("m9-ingest-consumer", block_ms=20)
                assert len(jobs) == 1
                processed = await process_knowledge_source(
                    session_factory,
                    storage,
                    gateway,
                    jobs[0].source_id,
                )
                assert processed is True
                replay = await process_knowledge_source(
                    session_factory,
                    storage,
                    gateway,
                    jobs[0].source_id,
                )
                assert replay is False
                await queue.acknowledge(jobs[0].stream_id)

                no_permission = await runtime.retrieve_for_agent(
                    tenant_id=tenant_a,
                    agent_id=unassigned_agent_id,
                    query_text="What is the refund policy?",
                )
                assert no_permission.context == ""
                cross_tenant = await runtime.retrieve_for_agent(
                    tenant_id=tenant_b,
                    agent_id=agent_id,
                    query_text="What is the refund policy?",
                )
                assert cross_tenant.context == ""

                retrieval = await runtime.retrieve_for_agent(
                    tenant_id=tenant_a,
                    agent_id=agent_id,
                    query_text="What is the refund policy?",
                )
                assert "[K1]" in retrieval.context
                assert "policy.xlsx" in retrieval.context
                assert "30 days" in retrieval.context
                assert retrieval.evidence
                return retrieval.context, retrieval.evidence[0].chunk_id
            finally:
                await redis_client.aclose()
                await engine.dispose()

        direct_context, selected_chunk_id = asyncio.run(ingest_and_retrieve())
        assert "[K1]" in direct_context

        with Session(SYNC_ENGINE) as db:
            source = db.get(KnowledgeSource, source_id)
            assert source is not None
            assert source.status == KnowledgeSourceStatus.READY.value
            assert source.chunk_count > 0
            assert source.embedding_provider == "mock"
            assert source.embedding_model_id == "mock-embedding"
            assert source.embedding_dimension == 3
            chunks = list(
                db.scalars(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.source_id == source_id)
                    .order_by(KnowledgeChunk.ordinal)
                ).all()
            )
            assert chunks
            assert all(chunk.tenant_id == tenant_a for chunk in chunks)
            assert all(chunk.knowledge_base_id == base_id for chunk in chunks)
            assert any(chunk.id == selected_chunk_id for chunk in chunks)
            traces = list(
                db.scalars(
                    select(KnowledgeRetrievalTrace).where(
                        KnowledgeRetrievalTrace.tenant_id == tenant_a
                    )
                ).all()
            )
            assert len(traces) == 1
            expected_hash = hashlib.sha256(b"What is the refund policy?").hexdigest()
            assert traces[0].query_sha256 == expected_hash
            assert "What is the refund policy?" not in repr(traces[0].selected_chunks)
            assert traces[0].selected_chunks[0]["chunk_id"] == str(selected_chunk_id)
            assert (
                db.scalar(text("SELECT extname FROM pg_extension WHERE extname = 'vector'"))
                == "vector"
            )

        login(client, "owner-a@example.com", "owner knowledge password a")
        channel_id = create_channel(client, tenant_a)
        assigned = client.put(
            f"/api/v1/tenants/{tenant_a}/agents/assignments/channels/{channel_id}",
            json={"agent_id": str(agent_id)},
        )
        assert assigned.status_code == 200
        webhook = post_webhook(client, channel_id, stored_webhook_secret(channel_id))
        assert webhook.status_code == 200

        async def run_agent() -> None:
            engine, session_factory = create_database(TEST_SETTINGS)
            redis_client = create_channel_redis(TEST_SETTINGS)
            queue = ChannelJobQueue(redis_client)
            gateway = AIGateway(AIProviderRegistry((ai_provider,)), session_factory)
            runtime = KnowledgeRuntime(session_factory, gateway)
            try:
                await queue.ensure_group()
                jobs = await queue.consume("m9-agent-consumer", block_ms=20)
                assert len(jobs) == 1
                await process_job(
                    jobs[0],
                    queue,
                    ChannelRegistry((channel_adapter,)),
                    gateway,
                    TEST_SETTINGS,
                    session_factory,
                    knowledge_runtime=runtime,
                )
            finally:
                await redis_client.aclose()
                await engine.dispose()

        asyncio.run(run_agent())
        assert len(channel_adapter.send_calls) == 1
        assert channel_adapter.send_calls[0][2] == "Returns are accepted within 30 days [K1]."
        assert ai_provider.generation_requests
        rag_request = ai_provider.generation_requests[-1]
        assert "[RETRIEVED KNOWLEDGE" in rag_request.input_text
        assert "[K1]" in rag_request.input_text
        assert "30 days" in rag_request.input_text
        assert "untrusted evidence" in (rag_request.instructions or "")

        with Session(SYNC_ENGINE) as db:
            run = db.scalar(select(AgentRun).where(AgentRun.tenant_id == tenant_a))
            assert run is not None
            assert run.outbound_message_id is not None
            outbound = db.get(Message, run.outbound_message_id)
            assert outbound is not None
            assert outbound.text == "Returns are accepted within 30 days [K1]."
            trace = db.scalar(
                select(KnowledgeRetrievalTrace).where(
                    KnowledgeRetrievalTrace.tenant_id == tenant_a,
                    KnowledgeRetrievalTrace.agent_run_id == run.id,
                )
            )
            assert trace is not None
            assert trace.selected_chunks
            permission = db.scalar(
                select(AgentKnowledgePermission).where(
                    AgentKnowledgePermission.tenant_id == tenant_a,
                    AgentKnowledgePermission.agent_id == agent_id,
                    AgentKnowledgePermission.knowledge_base_id == base_id,
                )
            )
            assert permission is not None
            audit_actions = {
                item.action
                for item in db.scalars(
                    select(AuditEvent).where(AuditEvent.tenant_id == tenant_a)
                ).all()
            }
            assert "knowledge.base.created" in audit_actions
            assert "knowledge.source.uploaded" in audit_actions
            assert "knowledge.permission.granted" in audit_actions
    finally:
        close_client(client)


def test_retryable_storage_failure_requeues_and_terminal_parse_failure_marks_failed() -> None:
    tenant_id = seed_tenant_user(
        slug="knowledge-retry",
        name="Knowledge Retry",
        email="retry@example.com",
        password="retry knowledge password",
    )
    storage = FlakyReadStorage()
    client = open_client(storage)
    provider = KnowledgeAIProvider()
    try:
        login(client, "retry@example.com", "retry knowledge password")
        base_response = client.post(
            f"/api/v1/tenants/{tenant_id}/knowledge-bases",
            json={"name": "Retry Policies"},
        )
        assert base_response.status_code == 201
        base_id = UUID(base_response.json()["id"])
        seed_ai_profiles(tenant_id)

        upload = client.post(
            f"/api/v1/tenants/{tenant_id}/knowledge-bases/{base_id}/sources",
            files={"file": ("retry.xlsx", _xlsx_bytes(), XLSX_MEDIA_TYPE)},
        )
        assert upload.status_code == 202
        source_id = UUID(upload.json()["id"])

        async def run_retry() -> None:
            engine, session_factory = create_database(TEST_SETTINGS)
            gateway = AIGateway(AIProviderRegistry((provider,)), session_factory)
            try:
                with pytest.raises(KnowledgeRuntimeError) as exc_info:
                    await process_knowledge_source(session_factory, storage, gateway, source_id)
                assert exc_info.value.retryable is True
                with Session(SYNC_ENGINE) as db:
                    source = db.get(KnowledgeSource, source_id)
                    assert source is not None
                    assert source.status == KnowledgeSourceStatus.QUEUED.value
                    assert source.error_code == "knowledge_storage_transport_error"
                assert (
                    await process_knowledge_source(session_factory, storage, gateway, source_id)
                    is True
                )
            finally:
                await engine.dispose()

        asyncio.run(run_retry())

        invalid = client.post(
            f"/api/v1/tenants/{tenant_id}/knowledge-bases/{base_id}/sources",
            files={"file": ("invalid.xlsx", b"not-a-zip", XLSX_MEDIA_TYPE)},
        )
        assert invalid.status_code == 202
        invalid_source_id = UUID(invalid.json()["id"])

        async def run_terminal() -> None:
            engine, session_factory = create_database(TEST_SETTINGS)
            gateway = AIGateway(AIProviderRegistry((provider,)), session_factory)
            try:
                with pytest.raises(KnowledgeRuntimeError) as exc_info:
                    await process_knowledge_source(
                        session_factory,
                        storage,
                        gateway,
                        invalid_source_id,
                    )
                assert exc_info.value.retryable is False
                assert exc_info.value.code == "knowledge_xlsx_invalid"
            finally:
                await engine.dispose()

        asyncio.run(run_terminal())
        with Session(SYNC_ENGINE) as db:
            invalid_source = db.get(KnowledgeSource, invalid_source_id)
            assert invalid_source is not None
            assert invalid_source.status == KnowledgeSourceStatus.FAILED.value
            assert invalid_source.error_code == "knowledge_xlsx_invalid"
    finally:
        close_client(client)
