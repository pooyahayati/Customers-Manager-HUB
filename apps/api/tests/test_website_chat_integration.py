import asyncio
import base64
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import SecretStr
from redis import Redis
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from customers_manager_hub.agent_models import AgentRun, AgentRunStatus
from customers_manager_hub.ai_gateway import AIGateway, AIProviderRegistry, MockAIProviderAdapter
from customers_manager_hub.ai_models import AITaskProfile, AITaskRoute, AITaskType
from customers_manager_hub.channel_gateway import ChannelRegistry
from customers_manager_hub.channel_models import (
    ChannelInboundEvent,
    ChannelType,
    WebsiteChatSession,
)
from customers_manager_hub.channel_queue import (
    CHANNEL_JOB_STREAM,
    ChannelJobQueue,
    create_channel_redis,
)
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
from customers_manager_hub.website import WebsiteAdapter
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
    encryption_key=SecretStr(base64.urlsafe_b64encode(b"w" * 32).decode()),
)
SYNC_ENGINE = create_engine(TEST_SETTINGS.sqlalchemy_database_url)
SYNC_REDIS = Redis.from_url(REDIS_URL, decode_responses=True)  # pyright: ignore[reportUnknownMemberType]

pytestmark = pytest.mark.skipif(
    not RUN_DB_INTEGRATION,
    reason="Website Chat integration tests require RUN_DB_INTEGRATION=1",
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
    result = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert result.status_code == 200
    return result


def open_client() -> tuple[FastAPI, TestClient]:
    app = create_app(TEST_SETTINGS)
    client = TestClient(app)
    client.__enter__()
    return app, client


def close_client(client: TestClient) -> None:
    client.__exit__(None, None, None)


def create_website_channel(client: TestClient, tenant_id: UUID) -> UUID:
    result = client.post(
        f"/api/v1/tenants/{tenant_id}/channels/website",
        json={
            "name": "Website Support",
            "allowed_origins": ["https://shop.example.test"],
        },
    )
    assert result.status_code == 201
    body = result.json()
    assert body["channel_type"] == "website"
    assert body["allowed_origins"] == ["https://shop.example.test"]
    return UUID(body["id"])


def create_published_prompt(client: TestClient, tenant_id: UUID) -> UUID:
    created = client.post(
        f"/api/v1/tenants/{tenant_id}/prompts",
        json={"name": "Website Prompt", "content": "Answer Website visitors briefly."},
    )
    assert created.status_code == 201
    prompt_id = UUID(created.json()["id"])
    published = client.post(f"/api/v1/tenants/{tenant_id}/prompts/{prompt_id}/publish")
    assert published.status_code == 200
    return prompt_id


def configure_agent(client: TestClient, tenant_id: UUID, channel_id: UUID) -> None:
    prompt_id = create_published_prompt(client, tenant_id)
    created = client.post(
        f"/api/v1/tenants/{tenant_id}/agents",
        json={"name": "Website Agent", "prompt_id": str(prompt_id)},
    )
    assert created.status_code == 201
    assigned = client.put(
        f"/api/v1/tenants/{tenant_id}/agents/assignments/channels/{channel_id}",
        json={"agent_id": created.json()["id"]},
    )
    assert assigned.status_code == 200
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
                model_id="mock-website-model",
                priority=0,
                parameters={},
            )
        )
        db.commit()


async def process_one_website_job() -> None:
    engine, session_factory = create_database(TEST_SETTINGS)
    redis_client = create_channel_redis(TEST_SETTINGS)
    queue = ChannelJobQueue(redis_client)
    try:
        await queue.ensure_group()
        jobs = await queue.consume("website-consumer", block_ms=20)
        assert len(jobs) == 1
        await process_job(
            jobs[0],
            queue,
            ChannelRegistry((WebsiteAdapter(),)),
            AIGateway(
                AIProviderRegistry(
                    (MockAIProviderAdapter(key="mock", generation_text="Website reply"),)
                ),
                session_factory,
            ),
            TEST_SETTINGS,
            session_factory,
        )
    finally:
        await redis_client.aclose()
        await engine.dispose()


def test_website_origin_session_and_agent_pipeline() -> None:
    tenant_id = seed_tenant_user(
        slug="website-a",
        name="Website A",
        email="owner@example.com",
        password="owner website password",
    )
    viewer_tenant_id = seed_tenant_user(
        slug="website-b",
        name="Website B",
        email="viewer@example.com",
        password="viewer website password",
        role=TenantRole.VIEWER,
    )
    _, client = open_client()
    try:
        login(client, "owner@example.com", "owner website password")
        channel_id = create_website_channel(client, tenant_id)
        configure_agent(client, tenant_id, channel_id)

        generic = client.get(f"/api/v1/tenants/{tenant_id}/channels")
        assert generic.status_code == 200
        website_item = next(item for item in generic.json() if item["id"] == str(channel_id))
        assert website_item["credentials_configured"] is True
        assert website_item["supported_capabilities"] == ["outbound_text", "text"]

        rejected = client.post(
            f"/api/v1/public/website/{channel_id}/sessions",
            headers={"Origin": "https://evil.example.test"},
        )
        assert rejected.status_code == 403

        preflight = client.options(
            f"/api/v1/public/website/{channel_id}/sessions/placeholder/messages",
            headers={
                "Origin": "https://shop.example.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-website-session-token",
            },
        )
        assert preflight.status_code == 204
        assert preflight.headers["access-control-allow-origin"] == "https://shop.example.test"

        created_session = client.post(
            f"/api/v1/public/website/{channel_id}/sessions",
            headers={"Origin": "https://shop.example.test"},
        )
        assert created_session.status_code == 201
        assert created_session.headers["access-control-allow-origin"] == "https://shop.example.test"
        assert created_session.headers["cache-control"] == "no-store"
        session_id = UUID(created_session.json()["session_id"])
        session_token = created_session.json()["session_token"]

        with Session(SYNC_ENGINE) as db:
            stored = db.get(WebsiteChatSession, session_id)
            assert stored is not None
            assert stored.token_hash != session_token.encode()
            audit_text = repr([item.details for item in db.scalars(select(AuditEvent)).all()])
            assert session_token not in audit_text

        wrong_token = client.post(
            f"/api/v1/public/website/{channel_id}/sessions/{session_id}/messages",
            headers={
                "Origin": "https://shop.example.test",
                "X-Website-Session-Token": "wrong-token",
            },
            json={"client_message_id": str(uuid4()), "text": "Hello"},
        )
        assert wrong_token.status_code == 401
        assert wrong_token.headers["access-control-allow-origin"] == "https://shop.example.test"
        assert wrong_token.headers["cache-control"] == "no-store"

        with Session(SYNC_ENGINE) as db:
            stored = db.get(WebsiteChatSession, session_id)
            assert stored is not None
            stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            db.commit()

        expired = client.get(
            f"/api/v1/public/website/{channel_id}/sessions/{session_id}/messages",
            headers={
                "Origin": "https://shop.example.test",
                "X-Website-Session-Token": session_token,
            },
        )
        assert expired.status_code == 401
        assert expired.headers["access-control-allow-origin"] == "https://shop.example.test"
        assert expired.headers["cache-control"] == "no-store"

        with Session(SYNC_ENGINE) as db:
            stored = db.get(WebsiteChatSession, session_id)
            assert stored is not None
            stored.expires_at = datetime.now(UTC) + timedelta(days=1)
            db.commit()

        second_channel_id = create_website_channel(client, tenant_id)
        mismatched = client.get(
            f"/api/v1/public/website/{second_channel_id}/sessions/{session_id}/messages",
            headers={
                "Origin": "https://shop.example.test",
                "X-Website-Session-Token": session_token,
            },
        )
        assert mismatched.status_code == 401
        assert mismatched.headers["access-control-allow-origin"] == "https://shop.example.test"

        async def ensure_group() -> None:
            redis_client = create_channel_redis(TEST_SETTINGS)
            try:
                await ChannelJobQueue(redis_client).ensure_group()
            finally:
                await redis_client.aclose()

        asyncio.run(ensure_group())
        client_message_id = uuid4()
        accepted = client.post(
            f"/api/v1/public/website/{channel_id}/sessions/{session_id}/messages",
            headers={
                "Origin": "https://shop.example.test",
                "X-Website-Session-Token": session_token,
            },
            json={"client_message_id": str(client_message_id), "text": "Can you help?"},
        )
        assert accepted.status_code == 202
        assert accepted.headers["access-control-allow-origin"] == "https://shop.example.test"
        assert SYNC_REDIS.xlen(CHANNEL_JOB_STREAM) == 1

        with Session(SYNC_ENGINE) as db:
            event = db.scalar(
                select(ChannelInboundEvent).where(
                    ChannelInboundEvent.channel_account_id == channel_id
                )
            )
            assert event is not None
            redis_payload = repr(SYNC_REDIS.xrange(CHANNEL_JOB_STREAM))
            assert session_token not in redis_payload

        asyncio.run(process_one_website_job())
        assert SYNC_REDIS.xlen(CHANNEL_JOB_STREAM) == 0

        messages = client.get(
            f"/api/v1/public/website/{channel_id}/sessions/{session_id}/messages",
            headers={
                "Origin": "https://shop.example.test",
                "X-Website-Session-Token": session_token,
            },
        )
        assert messages.status_code == 200
        assert messages.headers["cache-control"] == "no-store"
        assert [item["text"] for item in messages.json()] == ["Can you help?", "Website reply"]
        assert [item["direction"] for item in messages.json()] == ["inbound", "outbound"]
        serialized_public = repr(messages.json())
        assert str(tenant_id) not in serialized_public
        assert "website_session_id" not in serialized_public

        with Session(SYNC_ENGINE) as db:
            assert db.scalar(select(func.count()).select_from(Message)) == 2
            inbound = db.scalar(
                select(Message).where(Message.direction == MessageDirection.INBOUND.value)
            )
            outbound = db.scalar(
                select(Message).where(Message.direction == MessageDirection.OUTBOUND.value)
            )
            assert inbound is not None and outbound is not None
            assert inbound.author_type == MessageAuthorType.CUSTOMER.value
            assert outbound.author_type == MessageAuthorType.AI.value
            assert outbound.text == "Website reply"
            assert outbound.external_metadata["channel_type"] == ChannelType.WEBSITE.value
            run = db.scalar(select(AgentRun).where(AgentRun.tenant_id == tenant_id))
            assert run is not None
            assert run.status == AgentRunStatus.SUCCEEDED.value
            assert run.outbound_message_id == outbound.id

        retry = client.post(
            f"/api/v1/public/website/{channel_id}/sessions/{session_id}/messages",
            headers={
                "Origin": "https://shop.example.test",
                "X-Website-Session-Token": session_token,
            },
            json={"client_message_id": str(client_message_id), "text": "Can you help?"},
        )
        assert retry.status_code == 202
        assert SYNC_REDIS.xlen(CHANNEL_JOB_STREAM) == 0
        with Session(SYNC_ENGINE) as db:
            assert db.scalar(select(func.count()).select_from(Message)) == 2

        seed_tenant_user(
            slug="unused",
            name="unused",
            email="viewer2@example.com",
            password="viewer2 password",
            role=TenantRole.VIEWER,
            tenant_id=tenant_id,
        )
        login(client, "viewer2@example.com", "viewer2 password")
        denied = client.put(
            f"/api/v1/tenants/{tenant_id}/channels/website/{channel_id}/origins",
            json={"allowed_origins": ["https://other.example.test"]},
        )
        assert denied.status_code == 403

        login(client, "viewer@example.com", "viewer website password")
        cross = client.get(f"/api/v1/tenants/{viewer_tenant_id}/channels/website/{channel_id}")
        assert cross.status_code == 404

        login(client, "owner@example.com", "owner website password")
        disabled = client.patch(
            f"/api/v1/tenants/{tenant_id}/channels/{channel_id}",
            json={"is_active": False},
        )
        assert disabled.status_code == 200
        inactive = client.get(
            f"/api/v1/public/website/{channel_id}/sessions/{session_id}/messages",
            headers={
                "Origin": "https://shop.example.test",
                "X-Website-Session-Token": session_token,
            },
        )
        assert inactive.status_code == 404
    finally:
        close_client(client)
