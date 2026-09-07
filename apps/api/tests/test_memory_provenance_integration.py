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
    extract_customer_memory_from_event,
    memory_dedupe_key,
)
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
    _truncate_database()
    yield
    _truncate_database()


def _truncate_database() -> None:
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


def _seed_tenant_user() -> tuple[UUID, UUID]:
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        tenant = Tenant(slug="memory-provenance", name="Memory Provenance")
        user = PlatformUser(
            email="owner@example.com",
            password_hash=hash_password("owner password"),
        )
        db.add_all((tenant, user))
        db.flush()
        db.add(
            TenantMembership(
                tenant_id=tenant.id,
                user_id=user.id,
                role=TenantRole.OWNER.value,
            )
        )
        db.commit()
        return tenant.id, user.id


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "owner password"},
    )
    assert response.status_code == 200


def _seed_extraction_event(
    *,
    tenant_id: UUID,
    occurred_at: datetime,
    text_value: str,
) -> tuple[UUID, UUID, UUID, UUID]:
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        contact = Contact(tenant_id=tenant_id, display_name="Memory Customer")
        db.add(contact)
        db.flush()
        conversation = Conversation(tenant_id=tenant_id, contact_id=contact.id)
        account = ChannelAccount(
            tenant_id=tenant_id,
            channel_type=ChannelType.TELEGRAM.value,
            name="Memory Bot",
            external_account_id="memory-bot",
        )
        db.add_all((conversation, account))
        db.flush()
        message = Message(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            direction=MessageDirection.INBOUND.value,
            author_type=MessageAuthorType.CUSTOMER.value,
            message_type=MessageType.TEXT.value,
            text=text_value,
            occurred_at=occurred_at,
        )
        db.add(message)
        db.flush()
        event = ChannelInboundEvent(
            tenant_id=tenant_id,
            channel_account_id=account.id,
            external_event_id=f"memory-{message.id}",
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
        return event.id, contact.id, conversation.id, message.id


def _gateway(payload: dict[str, object]) -> tuple[AIGateway, MockAIProviderAdapter]:
    adapter = MockAIProviderAdapter(structured_payload=payload)
    _, session_factory = create_database(TEST_SETTINGS)
    return AIGateway(AIProviderRegistry((adapter,)), session_factory), adapter


def test_verification_preserves_extracted_memory_provenance() -> None:
    tenant_id, _ = _seed_tenant_user()
    observed_at = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        contact = Contact(tenant_id=tenant_id, display_name="Provenance Customer")
        db.add(contact)
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
            text="I am interested in the enterprise plan.",
            occurred_at=observed_at,
        )
        db.add(message)
        db.flush()
        memory = CustomerMemoryItem(
            tenant_id=tenant_id,
            contact_id=contact.id,
            category=MemoryCategory.PRODUCT_INTEREST.value,
            value="Enterprise plan",
            dedupe_key=memory_dedupe_key(
                MemoryCategory.PRODUCT_INTEREST,
                "Enterprise plan",
            ),
            evidence_kind=MemoryEvidenceKind.INFERENCE.value,
            confidence=0.9,
            source_type=MemorySourceType.CUSTOMER_MESSAGE.value,
            source_message_id=message.id,
            source_conversation_id=conversation.id,
            observed_at=observed_at,
            expires_at=observed_at + timedelta(days=90),
        )
        db.add(memory)
        db.commit()
        contact_id = contact.id
        conversation_id = conversation.id
        message_id = message.id
        memory_id = memory.id

    with TestClient(create_app(TEST_SETTINGS)) as client:
        _login(client)
        response = client.patch(
            f"/api/v1/tenants/{tenant_id}/contacts/{contact_id}/memories/{memory_id}",
            json={"verified": True},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["source_type"] == MemorySourceType.CUSTOMER_MESSAGE.value
    assert body["source_message_id"] == str(message_id)
    assert body["source_conversation_id"] == str(conversation_id)
    assert body["created_by_user_id"] is None
    assert body["verified_at"] is not None


def test_extraction_never_overwrites_manual_memory() -> None:
    tenant_id, owner_id = _seed_tenant_user()
    occurred_at = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    event_id, contact_id, _, _ = _seed_extraction_event(
        tenant_id=tenant_id,
        occurred_at=occurred_at,
        text_value="Please answer in English.",
    )
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        manual = CustomerMemoryItem(
            tenant_id=tenant_id,
            contact_id=contact_id,
            category=MemoryCategory.PREFERRED_LANGUAGE.value,
            value="Persian",
            dedupe_key=memory_dedupe_key(MemoryCategory.PREFERRED_LANGUAGE, "Persian"),
            evidence_kind=MemoryEvidenceKind.FACT.value,
            confidence=1.0,
            source_type=MemorySourceType.MANUAL.value,
            created_by_user_id=owner_id,
            observed_at=occurred_at - timedelta(days=1),
            verified_at=occurred_at - timedelta(days=1),
            verified_by_user_id=owner_id,
        )
        db.add(manual)
        db.commit()
        memory_id = manual.id

    gateway, adapter = _gateway(
        {
            "items": [
                {
                    "category": "preferred_language",
                    "value": "English",
                    "evidence_kind": "fact",
                    "confidence": 0.99,
                }
            ]
        }
    )
    _, session_factory = create_database(TEST_SETTINGS)
    assert asyncio.run(extract_customer_memory_from_event(session_factory, gateway, event_id)) == 0
    assert adapter.calls == 1

    with Session(SYNC_ENGINE) as db:
        memory = db.get(CustomerMemoryItem, memory_id)
        assert memory is not None
        assert memory.value == "Persian"
        assert memory.source_type == MemorySourceType.MANUAL.value
        assert memory.created_by_user_id == owner_id
        assert (
            db.scalar(
                select(CustomerMemoryExtraction.id).where(
                    CustomerMemoryExtraction.tenant_id == tenant_id,
                    CustomerMemoryExtraction.source_message_id.is_not(None),
                )
            )
            is not None
        )


def test_stale_extraction_cannot_replace_newer_memory() -> None:
    tenant_id, _ = _seed_tenant_user()
    occurred_at = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    event_id, contact_id, conversation_id, _ = _seed_extraction_event(
        tenant_id=tenant_id,
        occurred_at=occurred_at,
        text_value="Please answer in English.",
    )
    newer_observation = occurred_at + timedelta(days=1)
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        current = CustomerMemoryItem(
            tenant_id=tenant_id,
            contact_id=contact_id,
            category=MemoryCategory.PREFERRED_LANGUAGE.value,
            value="Persian",
            dedupe_key=memory_dedupe_key(MemoryCategory.PREFERRED_LANGUAGE, "Persian"),
            evidence_kind=MemoryEvidenceKind.FACT.value,
            confidence=0.99,
            source_type=MemorySourceType.CUSTOMER_MESSAGE.value,
            source_conversation_id=conversation_id,
            observed_at=newer_observation,
            expires_at=newer_observation + timedelta(days=180),
        )
        db.add(current)
        db.commit()
        memory_id = current.id

    gateway, adapter = _gateway(
        {
            "items": [
                {
                    "category": "preferred_language",
                    "value": "English",
                    "evidence_kind": "fact",
                    "confidence": 0.99,
                }
            ]
        }
    )
    _, session_factory = create_database(TEST_SETTINGS)
    assert asyncio.run(extract_customer_memory_from_event(session_factory, gateway, event_id)) == 0
    assert adapter.calls == 1

    with Session(SYNC_ENGINE) as db:
        memory = db.get(CustomerMemoryItem, memory_id)
        assert memory is not None
        assert memory.value == "Persian"
        assert memory.observed_at == newer_observation
        assert memory.source_type == MemorySourceType.CUSTOMER_MESSAGE.value
