import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from customers_manager_hub.ai_gateway import AIGateway, AIProviderRegistry, MockAIProviderAdapter
from customers_manager_hub.ai_models import AITaskProfile, AITaskRoute, AITaskType
from customers_manager_hub.channel_models import (
    ChannelAccount,
    ChannelInboundEvent,
    ChannelInboundEventStatus,
    ChannelType,
)
from customers_manager_hub.config import Settings
from customers_manager_hub.database import create_database
from customers_manager_hub.main import create_app
from customers_manager_hub.memory_models import (
    CustomerMemoryExtraction,
    CustomerMemoryItem,
    MemoryCategory,
    MemoryEvidenceKind,
    MemorySourceType,
)
from customers_manager_hub.memory_runtime import (
    build_customer_memory_context,
    extract_customer_memory_from_event,
    memory_dedupe_key,
)
from customers_manager_hub.models import (
    AuditEvent,
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
                "TRUNCATE TABLE customer_memory_extractions, customer_memory_items, "
                "ai_execution_traces, ai_task_routes, ai_task_profiles, channel_inbound_events, "
                "conversation_channel_bindings, channel_account_capabilities, channel_credentials, "
                "channel_accounts, message_attachments, messages, conversations, external_identities, "
                "contacts, audit_events, auth_sessions, tenant_memberships, platform_users, tenants "
                "CASCADE"
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


def login(client: TestClient, email: str, password: str) -> None:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200


def test_memory_api_is_tenant_scoped_audited_and_soft_deletes() -> None:
    tenant_a, owner_a = seed_tenant_user(
        tenant_slug="memory-a",
        tenant_name="Memory A",
        email="owner-a@example.com",
        password="owner password a",
    )
    tenant_b, _ = seed_tenant_user(
        tenant_slug="memory-b",
        tenant_name="Memory B",
        email="owner-b@example.com",
        password="owner password b",
    )

    with TestClient(create_app(TEST_SETTINGS)) as client_a:
        login(client_a, "owner-a@example.com", "owner password a")
        contact = client_a.post(
            f"/api/v1/tenants/{tenant_a}/contacts",
            json={"display_name": "Returning Customer"},
        )
        assert contact.status_code == 201
        contact_id = contact.json()["id"]

        created = client_a.post(
            f"/api/v1/tenants/{tenant_a}/contacts/{contact_id}/memories",
            json={
                "category": "preferred_language",
                "value": "Persian",
                "evidence_kind": "fact",
                "confidence": 1.0,
            },
        )
        assert created.status_code == 201
        memory_id = created.json()["id"]
        assert created.json()["source_type"] == "manual"
        assert created.json()["verified_at"] is not None

        duplicate = client_a.post(
            f"/api/v1/tenants/{tenant_a}/contacts/{contact_id}/memories",
            json={
                "category": "preferred_language",
                "value": "Persian",
                "evidence_kind": "fact",
                "confidence": 1.0,
            },
        )
        assert duplicate.status_code == 409

        updated = client_a.patch(
            f"/api/v1/tenants/{tenant_a}/contacts/{contact_id}/memories/{memory_id}",
            json={
                "category": "detail_level",
                "value": "detailed",
                "confidence": 0.95,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["category"] == "detail_level"
        assert updated.json()["value"] == "detailed"

        deleted = client_a.delete(
            f"/api/v1/tenants/{tenant_a}/contacts/{contact_id}/memories/{memory_id}"
        )
        assert deleted.status_code == 204
        assert (
            client_a.get(f"/api/v1/tenants/{tenant_a}/contacts/{contact_id}/memories").json() == []
        )
        deleted_listing = client_a.get(
            f"/api/v1/tenants/{tenant_a}/contacts/{contact_id}/memories?include_deleted=true"
        )
        assert deleted_listing.status_code == 200
        assert deleted_listing.json()[0]["deleted_at"] is not None

    with TestClient(create_app(TEST_SETTINGS)) as client_b:
        login(client_b, "owner-b@example.com", "owner password b")
        assert (
            client_b.get(f"/api/v1/tenants/{tenant_b}/contacts/{contact_id}/memories").status_code
            == 404
        )

    with Session(SYNC_ENGINE) as db:
        actions = list(
            db.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.tenant_id == tenant_a,
                    AuditEvent.actor_user_id == owner_a,
                    AuditEvent.target_type == "customer_memory_item",
                )
            )
        )
        assert actions == [
            "customer_memory.created",
            "customer_memory.updated",
            "customer_memory.deleted",
        ]


def test_memory_context_excludes_untrusted_stale_and_deleted_items() -> None:
    tenant_id, owner_id = seed_tenant_user(
        tenant_slug="memory-context",
        tenant_name="Memory Context",
        email="owner@example.com",
        password="owner password",
    )
    now = datetime.now(UTC)
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        contact = Contact(tenant_id=tenant_id, display_name="Context Customer")
        db.add(contact)
        db.flush()
        conversation = Conversation(tenant_id=tenant_id, contact_id=contact.id)
        db.add(conversation)
        db.flush()
        rows = [
            CustomerMemoryItem(
                tenant_id=tenant_id,
                contact_id=contact.id,
                category=MemoryCategory.PREFERRED_LANGUAGE.value,
                value="Persian",
                dedupe_key="singleton",
                evidence_kind=MemoryEvidenceKind.FACT.value,
                confidence=0.95,
                source_type=MemorySourceType.CUSTOMER_MESSAGE.value,
                observed_at=now,
                expires_at=now + timedelta(days=30),
            ),
            CustomerMemoryItem(
                tenant_id=tenant_id,
                contact_id=contact.id,
                category=MemoryCategory.PRODUCT_INTEREST.value,
                value="Enterprise plan",
                dedupe_key=memory_dedupe_key(MemoryCategory.PRODUCT_INTEREST, "Enterprise plan"),
                evidence_kind=MemoryEvidenceKind.INFERENCE.value,
                confidence=0.98,
                source_type=MemorySourceType.CUSTOMER_MESSAGE.value,
                observed_at=now,
                expires_at=now + timedelta(days=30),
            ),
            CustomerMemoryItem(
                tenant_id=tenant_id,
                contact_id=contact.id,
                category=MemoryCategory.ISSUE.value,
                value="Old expired issue",
                dedupe_key=memory_dedupe_key(MemoryCategory.ISSUE, "Old expired issue"),
                evidence_kind=MemoryEvidenceKind.FACT.value,
                confidence=0.99,
                source_type=MemorySourceType.CUSTOMER_MESSAGE.value,
                observed_at=now - timedelta(days=365),
                expires_at=now - timedelta(days=1),
            ),
            CustomerMemoryItem(
                tenant_id=tenant_id,
                contact_id=contact.id,
                category=MemoryCategory.RESOLUTION.value,
                value="Deleted resolution",
                dedupe_key=memory_dedupe_key(MemoryCategory.RESOLUTION, "Deleted resolution"),
                evidence_kind=MemoryEvidenceKind.FACT.value,
                confidence=0.99,
                source_type=MemorySourceType.MANUAL.value,
                observed_at=now,
                verified_at=now,
                verified_by_user_id=owner_id,
                deleted_at=now,
                deleted_by_user_id=owner_id,
            ),
        ]
        db.add_all(rows)
        db.commit()
        conversation_id = conversation.id
        inference_id = rows[1].id

    _, session_factory = create_database(TEST_SETTINGS)
    context = asyncio.run(
        build_customer_memory_context(
            session_factory,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
        )
    )
    assert "CUSTOMER-STATED FACT | preferred_language: Persian" in context
    assert "Enterprise plan" not in context
    assert "Old expired issue" not in context
    assert "Deleted resolution" not in context

    with Session(SYNC_ENGINE) as db:
        inference = db.get(CustomerMemoryItem, inference_id)
        assert inference is not None
        inference.verified_at = now
        inference.verified_by_user_id = owner_id
        db.commit()

    context = asyncio.run(
        build_customer_memory_context(
            session_factory,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
        )
    )
    assert "APPROVED INFERENCE | product_interest: Enterprise plan" in context


def test_ai_memory_extraction_is_structured_and_idempotent() -> None:
    tenant_id, _ = seed_tenant_user(
        tenant_slug="memory-extract",
        tenant_name="Memory Extract",
        email="owner@example.com",
        password="owner password",
    )
    occurred_at = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        contact = Contact(tenant_id=tenant_id, display_name="Extraction Customer")
        db.add(contact)
        db.flush()
        conversation = Conversation(tenant_id=tenant_id, contact_id=contact.id)
        account = ChannelAccount(
            tenant_id=tenant_id,
            channel_type=ChannelType.TELEGRAM.value,
            name="Memory Test Bot",
            external_account_id="memory-test-bot",
        )
        db.add_all((conversation, account))
        db.flush()
        message = Message(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            direction=MessageDirection.INBOUND.value,
            author_type=MessageAuthorType.CUSTOMER.value,
            message_type=MessageType.TEXT.value,
            text="Please answer me in Persian. I am looking at your enterprise plan.",
            occurred_at=occurred_at,
        )
        db.add(message)
        db.flush()
        event = ChannelInboundEvent(
            tenant_id=tenant_id,
            channel_account_id=account.id,
            external_event_id="memory-event-1",
            event_type="message",
            status=ChannelInboundEventStatus.PROCESSED.value,
            message_id=message.id,
        )
        profile = AITaskProfile(
            tenant_id=tenant_id,
            task_type=AITaskType.CUSTOMER_MEMORY_EXTRACTION.value,
            timeout_seconds=30,
            attempts_per_route=1,
        )
        db.add_all((event, profile))
        db.flush()
        db.add(
            AITaskRoute(
                tenant_id=tenant_id,
                profile_id=profile.id,
                provider="mock",
                model_id="memory-model",
                priority=0,
                parameters={},
            )
        )
        db.commit()
        event_id = event.id
        message_id = message.id
        contact_id = contact.id

    adapter = MockAIProviderAdapter(
        structured_payload={
            "items": [
                {
                    "category": "preferred_language",
                    "value": "Persian",
                    "evidence_kind": "fact",
                    "confidence": 0.99,
                },
                {
                    "category": "product_interest",
                    "value": "Enterprise plan",
                    "evidence_kind": "inference",
                    "confidence": 0.9,
                },
            ]
        }
    )
    _, session_factory = create_database(TEST_SETTINGS)
    gateway = AIGateway(AIProviderRegistry((adapter,)), session_factory)

    assert asyncio.run(extract_customer_memory_from_event(session_factory, gateway, event_id)) == 2
    assert adapter.calls == 1
    assert asyncio.run(extract_customer_memory_from_event(session_factory, gateway, event_id)) == 0
    assert adapter.calls == 1

    with Session(SYNC_ENGINE) as db:
        memories = list(
            db.scalars(
                select(CustomerMemoryItem)
                .where(
                    CustomerMemoryItem.tenant_id == tenant_id,
                    CustomerMemoryItem.contact_id == contact_id,
                )
                .order_by(CustomerMemoryItem.category)
            )
        )
        assert len(memories) == 2
        by_category = {item.category: item for item in memories}
        language = by_category[MemoryCategory.PREFERRED_LANGUAGE.value]
        assert language.evidence_kind == MemoryEvidenceKind.FACT.value
        assert language.source_message_id == message_id
        assert language.expires_at == occurred_at + timedelta(days=180)
        interest = by_category[MemoryCategory.PRODUCT_INTEREST.value]
        assert interest.evidence_kind == MemoryEvidenceKind.INFERENCE.value
        assert interest.verified_at is None
        assert (
            db.scalar(
                select(CustomerMemoryExtraction.id).where(
                    CustomerMemoryExtraction.tenant_id == tenant_id,
                    CustomerMemoryExtraction.source_message_id == message_id,
                )
            )
            is not None
        )
