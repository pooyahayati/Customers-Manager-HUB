import asyncio
import base64
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis import Redis
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from customers_manager_hub.agent_models import AgentRun, AgentRunStatus
from customers_manager_hub.ai_gateway import (
    AIGateway,
    AIProviderRegistry,
    AIUsage,
    GenerationRequest,
    GenerationResult,
    MockAIProviderAdapter,
    TranscriptionRequest,
    TranscriptionResult,
)
from customers_manager_hub.ai_models import AIExecutionTrace, AITaskProfile, AITaskRoute, AITaskType
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
from customers_manager_hub.channel_runtime import process_channel_event
from customers_manager_hub.channel_security import decrypt_channel_secret
from customers_manager_hub.config import Settings
from customers_manager_hub.database import create_database
from customers_manager_hub.main import create_app
from customers_manager_hub.models import (
    Contact,
    Conversation,
    Message,
    MessageAttachment,
    MessageAuthorType,
    MessageDirection,
    MessageType,
    PlatformUser,
    Tenant,
    TenantMembership,
    TenantRole,
)
from customers_manager_hub.security import hash_password
from customers_manager_hub.voice_runtime import (
    TelegramVoiceMediaDownloader,
    VoiceTranscriptionError,
    process_voice_transcription,
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
    encryption_key=SecretStr(base64.urlsafe_b64encode(b"v" * 32).decode()),
    telegram_webhook_base_url="https://voice.example.test",
)
SYNC_ENGINE = create_engine(TEST_SETTINGS.sqlalchemy_database_url)
SYNC_REDIS = Redis.from_url(REDIS_URL, decode_responses=True)  # pyright: ignore[reportUnknownMemberType]

pytestmark = pytest.mark.skipif(
    not RUN_DB_INTEGRATION,
    reason="Voice integration tests require RUN_DB_INTEGRATION=1",
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
        self.send_calls: list[tuple[str, str, str]] = []

    async def validate_account(self, access_secret: str) -> ChannelAccountIdentity:
        assert access_secret == "810001:voice-test-token"
        return ChannelAccountIdentity(
            external_account_id="810001",
            username="voice_test_bot",
            display_name="Voice Test Bot",
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
        assert access_secret == "810001:voice-test-token"
        assert external_media_id == "voice-file-1"
        return ChannelMediaReference(
            external_media_id=external_media_id,
            external_unique_id="voice-unique-1",
            file_path="voice/file_1.oga",
            size_bytes=11,
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
            external_message_id="voice-out-1",
            occurred_at=datetime.now(UTC),
        )


class GeminiVoiceProvider(MockAIProviderAdapter):
    def __init__(self) -> None:
        super().__init__(key="gemini")
        self.transcription_requests: list[TranscriptionRequest] = []
        self.generation_requests: list[GenerationRequest] = []
        self.transcription_calls = 0
        self.generation_calls = 0

    async def transcribe(
        self,
        model_id: str,
        request: TranscriptionRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> TranscriptionResult:
        del timeout_seconds
        self.transcription_calls += 1
        self.transcription_requests.append(request)
        assert model_id == "gemini-3.5-transcribe"
        assert parameters == {"language_codes": [], "mode": "verbatim"}
        assert request.audio == b"voice-bytes"
        return TranscriptionResult(
            provider=self.key,
            model_id=model_id,
            text="Where is my order?",
            usage=AIUsage(audio_seconds=4.0),
            provider_request_id="gemini-transcription-1",
        )

    async def generate(
        self,
        model_id: str,
        request: GenerationRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> GenerationResult:
        del parameters, timeout_seconds
        self.generation_calls += 1
        self.generation_requests.append(request)
        assert model_id == "gemini-customer-response"
        return GenerationResult(
            provider=self.key,
            model_id=model_id,
            text="I can help check your order.",
            structured=None,
            usage=AIUsage(input_tokens=8, output_tokens=7, total_tokens=15),
            provider_request_id="gemini-response-1",
        )


@pytest.fixture(autouse=True)
def clean_runtime() -> Iterator[None]:
    with SYNC_ENGINE.begin() as connection:
        connection.execute(text("TRUNCATE TABLE platform_users, tenants CASCADE"))
    SYNC_REDIS.delete(CHANNEL_JOB_STREAM)
    yield
    SYNC_REDIS.delete(CHANNEL_JOB_STREAM)
    with SYNC_ENGINE.begin() as connection:
        connection.execute(text("TRUNCATE TABLE platform_users, tenants CASCADE"))


def seed_tenant_user() -> UUID:
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        tenant = Tenant(slug="voice-e2e", name="Voice E2E")
        db.add(tenant)
        db.flush()
        user = PlatformUser(
            email="voice-owner@example.com",
            password_hash=hash_password("voice owner password"),
        )
        db.add(user)
        db.flush()
        db.add(
            TenantMembership(
                tenant_id=tenant.id,
                user_id=user.id,
                role=TenantRole.OWNER.value,
            )
        )
        db.commit()
        return tenant.id


def login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "voice-owner@example.com", "password": "voice owner password"},
    )
    assert response.status_code == 200


def create_runtime_configuration(client: TestClient, tenant_id: UUID) -> tuple[UUID, str]:
    channel = client.post(
        f"/api/v1/tenants/{tenant_id}/channels/telegram",
        json={
            "name": "Voice Bot",
            "bot_token": "810001:voice-test-token",
            "enabled_inbound_types": ["text", "voice"],
        },
    )
    assert channel.status_code == 201
    channel_id = UUID(channel.json()["id"])

    prompt = client.post(
        f"/api/v1/tenants/{tenant_id}/prompts",
        json={"name": "Voice Prompt", "content": "Answer the customer's request concisely."},
    )
    assert prompt.status_code == 201
    prompt_id = UUID(prompt.json()["id"])
    assert (
        client.post(f"/api/v1/tenants/{tenant_id}/prompts/{prompt_id}/publish").status_code == 200
    )

    agent = client.post(
        f"/api/v1/tenants/{tenant_id}/agents",
        json={"name": "Voice Agent", "prompt_id": str(prompt_id)},
    )
    assert agent.status_code == 201
    agent_id = UUID(agent.json()["id"])
    assignment = client.put(
        f"/api/v1/tenants/{tenant_id}/agents/assignments/channels/{channel_id}",
        json={"agent_id": str(agent_id)},
    )
    assert assignment.status_code == 200

    route_configs: tuple[tuple[AITaskType, str, dict[str, object]], ...] = (
        (
            AITaskType.VOICE_TRANSCRIPTION,
            "gemini-3.5-transcribe",
            {"language_codes": [], "mode": "verbatim"},
        ),
        (AITaskType.CUSTOMER_RESPONSE, "gemini-customer-response", {}),
    )
    with Session(SYNC_ENGINE) as db:
        for task_type, model_id, parameters in route_configs:
            profile = AITaskProfile(
                tenant_id=tenant_id,
                task_type=task_type.value,
                timeout_seconds=60,
                attempts_per_route=1,
            )
            db.add(profile)
            db.flush()
            db.add(
                AITaskRoute(
                    tenant_id=tenant_id,
                    profile_id=profile.id,
                    provider="gemini",
                    model_id=model_id,
                    priority=0,
                    parameters=parameters,
                )
            )
        db.commit()

        account = db.get(ChannelAccount, channel_id)
        assert account is not None
        credential = db.scalar(
            select(ChannelCredential).where(
                ChannelCredential.channel_account_id == channel_id,
                ChannelCredential.kind == ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET.value,
            )
        )
        assert credential is not None
        webhook_secret = decrypt_channel_secret(
            TEST_SETTINGS,
            tenant_id,
            channel_id,
            ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET,
            ciphertext=credential.ciphertext,
            nonce=credential.nonce,
            key_version=credential.key_version,
        )
    return channel_id, webhook_secret


def voice_update() -> dict[str, object]:
    return {
        "update_id": 88001,
        "message": {
            "message_id": 88002,
            "date": 1_700_880_002,
            "chat": {"id": 88, "type": "private"},
            "from": {"id": 88, "is_bot": False, "first_name": "Voice"},
            "voice": {
                "file_id": "voice-file-1",
                "file_unique_id": "voice-unique-1",
                "duration": 4,
                "mime_type": "audio/ogg",
                "file_size": 11,
            },
        },
    }


def test_gemini_first_route_falls_back_to_openai_on_retryable_failure() -> None:
    tenant_id = seed_tenant_user()
    with Session(SYNC_ENGINE) as db:
        profile = AITaskProfile(
            tenant_id=tenant_id,
            task_type=AITaskType.VOICE_TRANSCRIPTION.value,
            timeout_seconds=30,
            attempts_per_route=1,
        )
        db.add(profile)
        db.flush()
        db.add_all(
            [
                AITaskRoute(
                    tenant_id=tenant_id,
                    profile_id=profile.id,
                    provider="gemini",
                    model_id="gemini-3.5-transcribe",
                    priority=0,
                    parameters={},
                ),
                AITaskRoute(
                    tenant_id=tenant_id,
                    profile_id=profile.id,
                    provider="openai",
                    model_id="gpt-4o-mini-transcribe",
                    priority=1,
                    parameters={},
                ),
            ]
        )
        db.commit()

    async def run() -> None:
        engine, session_factory = create_database(TEST_SETTINGS)
        gemini = MockAIProviderAdapter(key="gemini", failures_before_success=1)
        openai = MockAIProviderAdapter(key="openai", transcription_text="fallback transcript")
        gateway = AIGateway(AIProviderRegistry((gemini, openai)), session_factory)
        try:
            result = await gateway.transcribe(
                tenant_id,
                TranscriptionRequest(audio=b"audio", filename="voice.ogg", mime_type="audio/ogg"),
            )
            assert result.provider == "openai"
            assert result.text == "fallback transcript"
            assert gemini.calls == 1
            assert openai.calls == 1
        finally:
            await engine.dispose()

    asyncio.run(run())
    with Session(SYNC_ENGINE) as db:
        traces = list(
            db.scalars(
                select(AIExecutionTrace)
                .where(AIExecutionTrace.tenant_id == tenant_id)
                .order_by(AIExecutionTrace.created_at, AIExecutionTrace.id)
            ).all()
        )
        assert [(trace.provider, trace.status) for trace in traces] == [
            ("gemini", "failed"),
            ("openai", "succeeded"),
        ]


def test_telegram_voice_transcribes_with_gemini_then_runs_normal_agent_pipeline_once() -> None:
    tenant_id = seed_tenant_user()
    adapter = FakeTelegramAdapter()
    app = create_app(TEST_SETTINGS)
    client = TestClient(app)
    client.__enter__()
    app.state.channel_registry = ChannelRegistry((adapter,))
    try:
        login(client)
        channel_id, webhook_secret = create_runtime_configuration(client, tenant_id)
        accepted = client.post(
            f"/api/v1/webhooks/telegram/{channel_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": webhook_secret},
            json=voice_update(),
        )
        assert accepted.status_code == 200

        provider = GeminiVoiceProvider()
        download_calls = 0

        def download_handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal download_calls
            download_calls += 1
            assert request.url.path == "/file/bot810001:voice-test-token/voice/file_1.oga"
            return httpx2.Response(200, request=request, content=b"voice-bytes")

        async def run_job() -> UUID:
            engine, session_factory = create_database(TEST_SETTINGS)
            redis_client = create_channel_redis(TEST_SETTINGS)
            queue = ChannelJobQueue(redis_client, claim_idle_ms=0)
            try:
                await queue.ensure_group()
                jobs = await queue.consume("voice-worker", block_ms=1)
                assert len(jobs) == 1
                async with httpx2.AsyncClient(
                    transport=httpx2.MockTransport(download_handler)
                ) as http_client:
                    downloader = TelegramVoiceMediaDownloader(http_client)
                    await process_job(
                        jobs[0],
                        queue,
                        ChannelRegistry((adapter,)),
                        AIGateway(AIProviderRegistry((provider,)), session_factory),
                        TEST_SETTINGS,
                        session_factory,
                        voice_downloader=downloader,
                    )
                event = await session_factory().__aenter__()
                try:
                    inbound_event = await event.scalar(
                        select(ChannelInboundEvent).where(
                            ChannelInboundEvent.tenant_id == tenant_id,
                            ChannelInboundEvent.external_event_id == "88001",
                        )
                    )
                    assert inbound_event is not None
                    return inbound_event.id
                finally:
                    await event.close()
            finally:
                await redis_client.aclose()
                await engine.dispose()

        event_id = asyncio.run(run_job())
        assert provider.transcription_calls == 1
        assert provider.generation_calls == 1
        assert download_calls == 1
        assert adapter.send_calls[-1][2] == "I can help check your order."
        assert SYNC_REDIS.xlen(CHANNEL_JOB_STREAM) == 0

        with Session(SYNC_ENGINE) as db:
            inbound = db.scalar(
                select(Message).where(
                    Message.tenant_id == tenant_id,
                    Message.message_type == MessageType.VOICE.value,
                )
            )
            assert inbound is not None
            assert inbound.text == "Where is my order?"
            attachment = db.scalar(
                select(MessageAttachment).where(MessageAttachment.message_id == inbound.id)
            )
            assert attachment is not None
            assert attachment.media_metadata["transcription_status"] == "succeeded"
            assert attachment.media_metadata["transcription_provider"] == "gemini"
            assert attachment.media_metadata["transcription_model_id"] == "gemini-3.5-transcribe"
            assert "voice-test-token" not in repr(attachment.media_metadata)
            run = db.scalar(select(AgentRun).where(AgentRun.inbound_message_id == inbound.id))
            assert run is not None
            assert run.status == AgentRunStatus.SUCCEEDED.value
            outbound = db.get(Message, run.outbound_message_id)
            assert outbound is not None
            assert outbound.direction == MessageDirection.OUTBOUND.value
            assert outbound.text == "I can help check your order."
            traces = list(
                db.scalars(
                    select(AIExecutionTrace)
                    .where(AIExecutionTrace.tenant_id == tenant_id)
                    .order_by(AIExecutionTrace.created_at, AIExecutionTrace.id)
                ).all()
            )
            assert [trace.task_type for trace in traces] == [
                AITaskType.VOICE_TRANSCRIPTION.value,
                AITaskType.CUSTOMER_RESPONSE.value,
            ]
            assert traces[0].provider == "gemini"
            assert traces[0].model_id == "gemini-3.5-transcribe"

        async def retry_transcription() -> None:
            engine, session_factory = create_database(TEST_SETTINGS)
            try:
                async with httpx2.AsyncClient(
                    transport=httpx2.MockTransport(download_handler)
                ) as http_client:
                    await process_voice_transcription(
                        session_factory,
                        AIGateway(AIProviderRegistry((provider,)), session_factory),
                        TEST_SETTINGS,
                        TelegramVoiceMediaDownloader(http_client),
                        event_id,
                    )
            finally:
                await engine.dispose()

        asyncio.run(retry_transcription())
        assert provider.transcription_calls == 1
        assert download_calls == 1
    finally:
        client.__exit__(None, None, None)


def test_voice_runtime_rejects_cross_tenant_message_reference() -> None:
    tenant_id = seed_tenant_user()
    adapter = FakeTelegramAdapter()
    app = create_app(TEST_SETTINGS)
    client = TestClient(app)
    client.__enter__()
    app.state.channel_registry = ChannelRegistry((adapter,))
    try:
        login(client)
        channel_id, webhook_secret = create_runtime_configuration(client, tenant_id)
        accepted = client.post(
            f"/api/v1/webhooks/telegram/{channel_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": webhook_secret},
            json=voice_update(),
        )
        assert accepted.status_code == 200

        with Session(SYNC_ENGINE, expire_on_commit=False) as db:
            event = db.scalar(
                select(ChannelInboundEvent).where(
                    ChannelInboundEvent.tenant_id == tenant_id,
                    ChannelInboundEvent.external_event_id == "88001",
                )
            )
            assert event is not None
            tenant_b = Tenant(slug="voice-cross-tenant", name="Voice Cross Tenant")
            db.add(tenant_b)
            db.flush()
            contact_b = Contact(tenant_id=tenant_b.id, display_name="Other Tenant Customer")
            db.add(contact_b)
            db.flush()
            conversation_b = Conversation(tenant_id=tenant_b.id, contact_id=contact_b.id)
            db.add(conversation_b)
            db.flush()
            message_b = Message(
                tenant_id=tenant_b.id,
                conversation_id=conversation_b.id,
                direction=MessageDirection.INBOUND.value,
                author_type=MessageAuthorType.CUSTOMER.value,
                message_type=MessageType.VOICE.value,
                occurred_at=datetime.now(UTC),
            )
            db.add(message_b)
            db.flush()
            event.message_id = message_b.id
            event_id = event.id
            db.commit()

        provider = GeminiVoiceProvider()
        download_calls = 0

        def unexpected_download(request: httpx2.Request) -> httpx2.Response:
            nonlocal download_calls
            download_calls += 1
            return httpx2.Response(500, request=request)

        async def run() -> None:
            engine, session_factory = create_database(TEST_SETTINGS)
            try:
                async with httpx2.AsyncClient(
                    transport=httpx2.MockTransport(unexpected_download)
                ) as http_client:
                    with pytest.raises(VoiceTranscriptionError) as error:
                        await process_voice_transcription(
                            session_factory,
                            AIGateway(AIProviderRegistry((provider,)), session_factory),
                            TEST_SETTINGS,
                            TelegramVoiceMediaDownloader(http_client),
                            event_id,
                        )
                    assert error.value.code == "voice_message_missing"
                    assert error.value.retryable is False
            finally:
                await engine.dispose()

        asyncio.run(run())
        assert provider.transcription_calls == 0
        assert download_calls == 0
    finally:
        client.__exit__(None, None, None)


def test_missing_voice_credential_is_terminal_after_media_resolution() -> None:
    tenant_id = seed_tenant_user()
    adapter = FakeTelegramAdapter()
    app = create_app(TEST_SETTINGS)
    client = TestClient(app)
    client.__enter__()
    app.state.channel_registry = ChannelRegistry((adapter,))
    try:
        login(client)
        channel_id, webhook_secret = create_runtime_configuration(client, tenant_id)
        accepted = client.post(
            f"/api/v1/webhooks/telegram/{channel_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": webhook_secret},
            json=voice_update(),
        )
        assert accepted.status_code == 200
        with Session(SYNC_ENGINE) as db:
            event = db.scalar(
                select(ChannelInboundEvent).where(
                    ChannelInboundEvent.tenant_id == tenant_id,
                    ChannelInboundEvent.external_event_id == "88001",
                )
            )
            assert event is not None
            event_id = event.id

        async def resolve_media() -> None:
            engine, session_factory = create_database(TEST_SETTINGS)
            try:
                await process_channel_event(
                    session_factory,
                    ChannelRegistry((adapter,)),
                    TEST_SETTINGS,
                    event_id,
                )
            finally:
                await engine.dispose()

        asyncio.run(resolve_media())
        with Session(SYNC_ENGINE) as db:
            credential = db.scalar(
                select(ChannelCredential).where(
                    ChannelCredential.tenant_id == tenant_id,
                    ChannelCredential.channel_account_id == channel_id,
                    ChannelCredential.kind == ChannelCredentialKind.TELEGRAM_BOT_TOKEN.value,
                )
            )
            assert credential is not None
            db.delete(credential)
            db.commit()

        provider = GeminiVoiceProvider()
        download_calls = 0

        def unexpected_download(request: httpx2.Request) -> httpx2.Response:
            nonlocal download_calls
            download_calls += 1
            return httpx2.Response(500, request=request)

        async def transcribe() -> None:
            engine, session_factory = create_database(TEST_SETTINGS)
            try:
                async with httpx2.AsyncClient(
                    transport=httpx2.MockTransport(unexpected_download)
                ) as http_client:
                    with pytest.raises(VoiceTranscriptionError) as error:
                        await process_voice_transcription(
                            session_factory,
                            AIGateway(AIProviderRegistry((provider,)), session_factory),
                            TEST_SETTINGS,
                            TelegramVoiceMediaDownloader(http_client),
                            event_id,
                        )
                    assert error.value.code == "channel_credential_missing"
                    assert error.value.retryable is False
            finally:
                await engine.dispose()

        asyncio.run(transcribe())
        assert provider.transcription_calls == 0
        assert download_calls == 0
    finally:
        client.__exit__(None, None, None)
