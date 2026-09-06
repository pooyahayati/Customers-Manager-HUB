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
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

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
    ChannelInboundEventStatus,
    ChannelType,
    ConversationChannelBinding,
)
from customers_manager_hub.channel_queue import (
    CHANNEL_JOB_STREAM,
    ChannelJobQueue,
    create_channel_redis,
)
from customers_manager_hub.channel_runtime import dispatch_text, process_channel_event
from customers_manager_hub.channel_security import decrypt_channel_secret
from customers_manager_hub.config import Settings
from customers_manager_hub.database import create_database
from customers_manager_hub.main import create_app
from customers_manager_hub.models import (
    AuditEvent,
    Contact,
    ExternalIdentity,
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

RUN_DB_INTEGRATION = os.environ.get("RUN_DB_INTEGRATION") == "1"
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://cmh:change-me@localhost:5432/customers_manager_hub",
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
TEST_ENCRYPTION_KEY = base64.urlsafe_b64encode(b"k" * 32).decode()
TEST_SETTINGS = Settings(
    app_env="test",
    database_url=DATABASE_URL,
    redis_url=REDIS_URL,
    encryption_key=SecretStr(TEST_ENCRYPTION_KEY),
    telegram_webhook_base_url="https://channels.example.test",
)
SYNC_ENGINE = create_engine(TEST_SETTINGS.sqlalchemy_database_url)
SYNC_REDIS = Redis.from_url(REDIS_URL, decode_responses=True)  # pyright: ignore[reportUnknownMemberType]

pytestmark = pytest.mark.skipif(
    not RUN_DB_INTEGRATION,
    reason="Channel integration tests require RUN_DB_INTEGRATION=1",
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
        self.validated_tokens: list[str] = []
        self.webhook_calls: list[tuple[str, str, str]] = []
        self.media_calls: list[tuple[str, str]] = []
        self.send_calls: list[tuple[str, str, str]] = []
        self.next_message_id = 900

    async def validate_account(self, access_secret: str) -> ChannelAccountIdentity:
        self.validated_tokens.append(access_secret)
        return ChannelAccountIdentity(
            external_account_id="777001",
            username="cmh_test_bot",
            display_name="CMH Test Bot",
        )

    async def register_webhook(
        self,
        access_secret: str,
        *,
        webhook_url: str,
        webhook_secret: str,
    ) -> None:
        self.webhook_calls.append((access_secret, webhook_url, webhook_secret))

    async def resolve_media(
        self,
        access_secret: str,
        external_media_id: str,
    ) -> ChannelMediaReference:
        self.media_calls.append((access_secret, external_media_id))
        return ChannelMediaReference(
            external_media_id=external_media_id,
            external_unique_id="voice-unique",
            file_path="voice/file_42.oga",
            size_bytes=2048,
        )

    async def send_text(
        self,
        access_secret: str,
        *,
        external_thread_id: str,
        text: str,
    ) -> ChannelSendResult:
        await asyncio.sleep(0.05)
        self.send_calls.append((access_secret, external_thread_id, text))
        self.next_message_id += 1
        return ChannelSendResult(
            external_message_id=str(self.next_message_id),
            occurred_at=datetime(2026, 9, 6, 19, 30, tzinfo=UTC),
        )


@pytest.fixture(autouse=True)
def clean_runtime() -> Iterator[None]:
    truncate_database()
    SYNC_REDIS.delete(CHANNEL_JOB_STREAM)
    yield
    SYNC_REDIS.delete(CHANNEL_JOB_STREAM)
    truncate_database()


def truncate_database() -> None:
    with SYNC_ENGINE.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE channel_inbound_events, conversation_channel_bindings, "
                "channel_account_capabilities, channel_credentials, channel_accounts, "
                "ai_execution_traces, ai_task_routes, ai_task_profiles, "
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
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response


def channel_client(adapter: FakeTelegramAdapter) -> tuple[object, TestClient]:
    app = create_app(TEST_SETTINGS)
    client = TestClient(app)
    client.__enter__()
    app.state.channel_registry = ChannelRegistry((adapter,))
    return app, client


def close_channel_client(client: TestClient) -> None:
    client.__exit__(None, None, None)


def stored_secret(account_id: UUID, kind: ChannelCredentialKind) -> str:
    with Session(SYNC_ENGINE) as db:
        account = db.get(ChannelAccount, account_id)
        assert account is not None
        credential = db.scalar(
            select(ChannelCredential).where(
                ChannelCredential.channel_account_id == account_id,
                ChannelCredential.kind == kind.value,
            )
        )
        assert credential is not None
        return decrypt_channel_secret(
            TEST_SETTINGS,
            account.tenant_id,
            account.id,
            kind,
            ciphertext=credential.ciphertext,
            nonce=credential.nonce,
            key_version=credential.key_version,
        )


def create_channel(client: TestClient, tenant_id: UUID) -> UUID:
    response = client.post(
        f"/api/v1/tenants/{tenant_id}/channels/telegram",
        json={
            "name": "Support Bot",
            "bot_token": "777001:test-bot-token",
            "enabled_inbound_types": ["text", "voice"],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["external_account_id"] == "777001"
    assert body["credentials_configured"] is True
    serialized = str(body)
    assert "test-bot-token" not in serialized
    return UUID(body["id"])


def telegram_text_update(
    update_id: int, message_id: int, text_value: str = "Hello"
) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": 1_700_000_000 + message_id,
            "chat": {"id": 42, "type": "private"},
            "from": {
                "id": 42,
                "is_bot": False,
                "first_name": "Ada",
                "last_name": "Lovelace",
                "username": "ada",
            },
            "text": text_value,
        },
    }


def telegram_voice_update(update_id: int, message_id: int) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": 1_700_000_000 + message_id,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False, "first_name": "Ada"},
            "voice": {
                "file_id": "voice-file",
                "file_unique_id": "voice-unique",
                "duration": 4,
                "mime_type": "audio/ogg",
                "file_size": 1024,
            },
        },
    }


def webhook(
    client: TestClient,
    account_id: UUID,
    secret: str,
    payload: dict[str, object],
) -> Response:
    return client.post(
        f"/api/v1/webhooks/telegram/{account_id}",
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        json=payload,
    )


def test_channel_configuration_encrypts_secrets_and_enforces_rbac_and_tenant_isolation() -> None:
    tenant_a, _ = seed_tenant_user(
        tenant_slug="channel-a",
        tenant_name="Channel A",
        email="owner-a@example.com",
        password="owner password tenant a",
    )
    tenant_b, _ = seed_tenant_user(
        tenant_slug="channel-b",
        tenant_name="Channel B",
        email="owner-b@example.com",
        password="owner password tenant b",
    )
    seed_tenant_user(
        tenant_slug="unused-viewer",
        tenant_name="unused",
        email="viewer-a@example.com",
        password="viewer password tenant a",
        role=TenantRole.VIEWER,
        tenant_id=tenant_a,
    )
    adapter = FakeTelegramAdapter()
    _, owner_client = channel_client(adapter)
    try:
        login(owner_client, "owner-a@example.com", "owner password tenant a")
        account_id = create_channel(owner_client, tenant_a)
        registration = owner_client.post(
            f"/api/v1/tenants/{tenant_a}/channels/{account_id}/register-webhook"
        )
        assert registration.status_code == 200
        assert registration.json()["url"].endswith(f"/api/v1/webhooks/telegram/{account_id}")
        assert len(adapter.webhook_calls) == 1
        assert adapter.webhook_calls[0][0] == "777001:test-bot-token"
        assert adapter.webhook_calls[0][2] == stored_secret(
            account_id, ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET
        )

        with Session(SYNC_ENGINE) as db:
            credentials = list(
                db.scalars(
                    select(ChannelCredential).where(
                        ChannelCredential.channel_account_id == account_id
                    )
                ).all()
            )
            assert len(credentials) == 2
            assert all(b"test-bot-token" not in credential.ciphertext for credential in credentials)
            audits = list(
                db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == tenant_a)).all()
            )
            audit_text = str([audit.details for audit in audits])
            assert "test-bot-token" not in audit_text
            assert stored_secret(account_id, ChannelCredentialKind.TELEGRAM_BOT_TOKEN) == (
                "777001:test-bot-token"
            )
    finally:
        close_channel_client(owner_client)

    _, viewer_client = channel_client(adapter)
    try:
        login(viewer_client, "viewer-a@example.com", "viewer password tenant a")
        denied = viewer_client.patch(
            f"/api/v1/tenants/{tenant_a}/channels/{account_id}", json={"is_active": False}
        )
        assert denied.status_code == 403
    finally:
        close_channel_client(viewer_client)

    _, tenant_b_client = channel_client(adapter)
    try:
        login(tenant_b_client, "owner-b@example.com", "owner password tenant b")
        cross_tenant = tenant_b_client.get(f"/api/v1/tenants/{tenant_b}/channels/{account_id}")
        assert cross_tenant.status_code == 404
    finally:
        close_channel_client(tenant_b_client)


def test_verified_text_webhook_persists_canonical_state_and_deduplicates() -> None:
    tenant_id, _ = seed_tenant_user(
        tenant_slug="telegram-text",
        tenant_name="Telegram Text",
        email="owner@example.com",
        password="owner password text",
    )
    adapter = FakeTelegramAdapter()
    _, client = channel_client(adapter)
    try:
        login(client, "owner@example.com", "owner password text")
        account_id = create_channel(client, tenant_id)
        secret = stored_secret(account_id, ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET)

        assert (
            webhook(client, account_id, "wrong-secret", telegram_text_update(100, 10)).status_code
            == 401
        )
        accepted = webhook(client, account_id, secret, telegram_text_update(100, 10))
        assert accepted.status_code == 200
        assert accepted.json() == {"ok": True}

        with Session(SYNC_ENGINE) as db:
            assert db.scalar(select(func.count()).select_from(Contact)) == 1
            assert db.scalar(select(func.count()).select_from(ExternalIdentity)) == 1
            assert db.scalar(select(func.count()).select_from(ConversationChannelBinding)) == 1
            assert db.scalar(select(func.count()).select_from(Message)) == 1
            assert db.scalar(select(func.count()).select_from(ChannelInboundEvent)) == 1
            message = db.scalar(select(Message))
            assert message is not None
            assert message.message_type == MessageType.TEXT.value
            assert message.text == "Hello"
            assert message.idempotency_key == f"telegram:{account_id}:chat:42:message:10"
            event = db.scalar(select(ChannelInboundEvent))
            assert event is not None
            assert event.status == ChannelInboundEventStatus.ENQUEUED.value
            event_id = event.id

        initial_stream_length = SYNC_REDIS.xlen(CHANNEL_JOB_STREAM)
        assert initial_stream_length == 1
        duplicate = webhook(client, account_id, secret, telegram_text_update(100, 10))
        assert duplicate.status_code == 200
        assert SYNC_REDIS.xlen(CHANNEL_JOB_STREAM) == initial_stream_length

        with Session(SYNC_ENGINE) as db:
            assert db.scalar(select(func.count()).select_from(Message)) == 1
            event = db.get(ChannelInboundEvent, event_id)
            assert event is not None
            event.status = ChannelInboundEventStatus.RECEIVED.value
            db.commit()

        retry_after_enqueue_failure = webhook(
            client, account_id, secret, telegram_text_update(100, 10)
        )
        assert retry_after_enqueue_failure.status_code == 200
        assert SYNC_REDIS.xlen(CHANNEL_JOB_STREAM) == initial_stream_length + 1
        with Session(SYNC_ENGINE) as db:
            assert db.scalar(select(func.count()).select_from(Message)) == 1
            assert db.scalar(select(func.count()).select_from(ChannelInboundEvent)) == 1
    finally:
        close_channel_client(client)


def test_disabled_capability_is_ignored_and_voice_worker_enriches_media() -> None:
    tenant_id, _ = seed_tenant_user(
        tenant_slug="telegram-voice",
        tenant_name="Telegram Voice",
        email="owner@example.com",
        password="owner password voice",
    )
    adapter = FakeTelegramAdapter()
    _, client = channel_client(adapter)
    try:
        login(client, "owner@example.com", "owner password voice")
        account_id = create_channel(client, tenant_id)
        secret = stored_secret(account_id, ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET)

        disable_text = client.patch(
            f"/api/v1/tenants/{tenant_id}/channels/{account_id}",
            json={"enabled_inbound_types": ["voice"]},
        )
        assert disable_text.status_code == 200
        ignored = webhook(client, account_id, secret, telegram_text_update(201, 21))
        assert ignored.status_code == 200
        with Session(SYNC_ENGINE) as db:
            assert db.scalar(select(func.count()).select_from(Message)) == 0
            ignored_event = db.scalar(select(ChannelInboundEvent))
            assert ignored_event is not None
            assert ignored_event.status == ChannelInboundEventStatus.IGNORED.value
            assert ignored_event.error_code == "capability_disabled"

        accepted_voice = webhook(client, account_id, secret, telegram_voice_update(202, 22))
        assert accepted_voice.status_code == 200
        with Session(SYNC_ENGINE) as db:
            event = db.scalar(
                select(ChannelInboundEvent).where(ChannelInboundEvent.external_event_id == "202")
            )
            assert event is not None
            event_id = event.id
            attachment = db.scalar(select(MessageAttachment))
            assert attachment is not None
            assert attachment.external_media_id == "voice-file"
            assert attachment.media_metadata["telegram_file_unique_id"] == "voice-unique"

        async def process() -> None:
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

        asyncio.run(process())
        assert adapter.media_calls == [("777001:test-bot-token", "voice-file")]
        with Session(SYNC_ENGINE) as db:
            event = db.get(ChannelInboundEvent, event_id)
            assert event is not None
            assert event.status == ChannelInboundEventStatus.PROCESSED.value
            attachment = db.scalar(select(MessageAttachment))
            assert attachment is not None
            assert attachment.size_bytes == 2048
            assert attachment.media_metadata["telegram_file_path"] == "voice/file_42.oga"
    finally:
        close_channel_client(client)


def test_redis_stream_consumer_ack_and_idle_reclaim() -> None:
    async def run() -> None:
        redis_client = create_channel_redis(TEST_SETTINGS)
        queue = ChannelJobQueue(redis_client, claim_idle_ms=0)
        try:
            await redis_client.delete(CHANNEL_JOB_STREAM)
            await queue.ensure_group()
            event_id = uuid4()
            await queue.enqueue(event_id)
            consumed = await queue.consume("consumer-a", block_ms=1)
            assert len(consumed) == 1
            assert consumed[0].event_id == event_id
            reclaimed = await queue.reclaim("consumer-b")
            assert len(reclaimed) == 1
            assert reclaimed[0].event_id == event_id
            await queue.acknowledge(reclaimed[0].stream_id)
            assert await redis_client.xlen(CHANNEL_JOB_STREAM) == 0
        finally:
            await redis_client.aclose()

    asyncio.run(run())


def test_outbound_text_dispatch_is_role_scoped_and_idempotent() -> None:
    tenant_id, _ = seed_tenant_user(
        tenant_slug="telegram-outbound",
        tenant_name="Telegram Outbound",
        email="owner@example.com",
        password="owner password outbound",
    )
    seed_tenant_user(
        tenant_slug="unused-supervisor",
        tenant_name="unused",
        email="supervisor@example.com",
        password="supervisor password",
        role=TenantRole.SUPERVISOR,
        tenant_id=tenant_id,
    )
    seed_tenant_user(
        tenant_slug="unused-viewer",
        tenant_name="unused",
        email="viewer@example.com",
        password="viewer password",
        role=TenantRole.VIEWER,
        tenant_id=tenant_id,
    )
    adapter = FakeTelegramAdapter()
    _, owner_client = channel_client(adapter)
    try:
        login(owner_client, "owner@example.com", "owner password outbound")
        account_id = create_channel(owner_client, tenant_id)
        secret = stored_secret(account_id, ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET)
        assert (
            webhook(owner_client, account_id, secret, telegram_text_update(301, 31)).status_code
            == 200
        )
        with Session(SYNC_ENGINE) as db:
            binding = db.scalar(select(ConversationChannelBinding))
            assert binding is not None
            conversation_id = binding.conversation_id
    finally:
        close_channel_client(owner_client)

    _, supervisor_client = channel_client(adapter)
    try:
        login(supervisor_client, "supervisor@example.com", "supervisor password")
        payload = {
            "conversation_id": str(conversation_id),
            "text": "Human reply",
            "idempotency_key": "telegram-outbound:test-1",
        }
        sent = supervisor_client.post(
            f"/api/v1/tenants/{tenant_id}/channels/{account_id}/send-text",
            json=payload,
        )
        assert sent.status_code == 200
        assert sent.json()["direction"] == MessageDirection.OUTBOUND.value
        assert sent.json()["external_message_id"] == "901"
        repeated = supervisor_client.post(
            f"/api/v1/tenants/{tenant_id}/channels/{account_id}/send-text",
            json=payload,
        )
        assert repeated.status_code == 200
        assert repeated.json()["id"] == sent.json()["id"]
        assert len(adapter.send_calls) == 1

        conflicting = supervisor_client.post(
            f"/api/v1/tenants/{tenant_id}/channels/{account_id}/send-text",
            json={**payload, "text": "Different reply"},
        )
        assert conflicting.status_code == 409
        assert len(adapter.send_calls) == 1

        async def send_concurrently() -> list[UUID]:
            engine, session_factory = create_database(TEST_SETTINGS)

            async def send_once() -> UUID:
                async with session_factory() as db:
                    message = await dispatch_text(
                        db,
                        ChannelRegistry((adapter,)),
                        TEST_SETTINGS,
                        tenant_id=tenant_id,
                        channel_account_id=account_id,
                        conversation_id=conversation_id,
                        text="Concurrent reply",
                        idempotency_key="telegram-outbound:concurrent",
                        author_type=MessageAuthorType.HUMAN,
                    )
                    return message.id

            try:
                return list(await asyncio.gather(send_once(), send_once()))
            finally:
                await engine.dispose()

        concurrent_ids = asyncio.run(send_concurrently())
        assert concurrent_ids[0] == concurrent_ids[1]
        assert len(adapter.send_calls) == 2
    finally:
        close_channel_client(supervisor_client)

    _, viewer_client = channel_client(adapter)
    try:
        login(viewer_client, "viewer@example.com", "viewer password")
        denied = viewer_client.post(
            f"/api/v1/tenants/{tenant_id}/channels/{account_id}/send-text",
            json={
                "conversation_id": str(conversation_id),
                "text": "denied",
                "idempotency_key": "telegram-outbound:viewer",
            },
        )
        assert denied.status_code == 403
    finally:
        close_channel_client(viewer_client)
