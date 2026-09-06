import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from customers_manager_hub.config import Settings
from customers_manager_hub.main import create_app
from customers_manager_hub.models import PlatformUser, Tenant, TenantMembership, TenantRole
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
def clean_customer_domain_tables() -> Iterator[None]:
    truncate_identity_database()
    yield
    truncate_identity_database()


def truncate_identity_database() -> None:
    with SYNC_ENGINE.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE message_attachments, messages, conversations, "
                "external_identities, contacts, audit_events, auth_sessions, "
                "tenant_memberships, platform_users, tenants CASCADE"
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
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    return response


def create_contact(client: TestClient, tenant_id: UUID, display_name: str) -> Response:
    return client.post(
        f"/api/v1/tenants/{tenant_id}/contacts",
        json={"display_name": display_name},
    )


def create_conversation(client: TestClient, tenant_id: UUID, contact_id: str) -> Response:
    return client.post(
        f"/api/v1/tenants/{tenant_id}/conversations",
        json={"contact_id": contact_id, "subject": "Order question"},
    )


def test_customer_history_is_tenant_scoped_and_message_retry_is_idempotent() -> None:
    tenant_a, _ = seed_tenant_user(
        tenant_slug="tenant-a",
        tenant_name="Tenant A",
        email="owner-a@example.com",
        password="owner password tenant a",
    )
    tenant_b, _ = seed_tenant_user(
        tenant_slug="tenant-b",
        tenant_name="Tenant B",
        email="owner-b@example.com",
        password="owner password tenant b",
    )

    with (
        TestClient(create_app(TEST_SETTINGS)) as client_a,
        TestClient(create_app(TEST_SETTINGS)) as client_b,
    ):
        login(client_a, "owner-a@example.com", "owner password tenant a")
        login(client_b, "owner-b@example.com", "owner password tenant b")

        contact_a = create_contact(client_a, tenant_a, "Alice Customer")
        assert contact_a.status_code == 201
        contact_a_id = contact_a.json()["id"]

        assert [
            item["id"] for item in client_a.get(f"/api/v1/tenants/{tenant_a}/contacts").json()
        ] == [contact_a_id]
        assert (
            client_b.get(f"/api/v1/tenants/{tenant_b}/contacts/{contact_a_id}").status_code == 404
        )

        identity = client_a.post(
            f"/api/v1/tenants/{tenant_a}/contacts/{contact_a_id}/identities",
            json={
                "namespace": "telegram",
                "external_id": "user-1001",
                "display_name": "Alice",
            },
        )
        assert identity.status_code == 201
        identity_id = identity.json()["id"]

        identity_retry = client_a.post(
            f"/api/v1/tenants/{tenant_a}/contacts/{contact_a_id}/identities",
            json={
                "namespace": "TELEGRAM",
                "external_id": "user-1001",
                "display_name": "Changed display name is ignored on retry",
            },
        )
        assert identity_retry.status_code == 200
        assert identity_retry.json()["id"] == identity_id

        second_contact = create_contact(client_a, tenant_a, "Another Person")
        assert second_contact.status_code == 201
        identity_conflict = client_a.post(
            f"/api/v1/tenants/{tenant_a}/contacts/{second_contact.json()['id']}/identities",
            json={"namespace": "telegram", "external_id": "user-1001"},
        )
        assert identity_conflict.status_code == 409

        conversation_a = create_conversation(client_a, tenant_a, contact_a_id)
        assert conversation_a.status_code == 201
        conversation_a_id = conversation_a.json()["id"]

        assert create_conversation(client_b, tenant_b, contact_a_id).status_code == 404
        assert (
            client_b.get(
                f"/api/v1/tenants/{tenant_b}/conversations/{conversation_a_id}"
            ).status_code
            == 404
        )

        other_identity = client_a.post(
            f"/api/v1/tenants/{tenant_a}/contacts/{second_contact.json()['id']}/identities",
            json={"namespace": "website:storefront", "external_id": "visitor-2"},
        )
        assert other_identity.status_code == 201

        mismatched_identity_message = client_a.post(
            f"/api/v1/tenants/{tenant_a}/conversations/{conversation_a_id}/messages",
            json={
                "external_identity_id": other_identity.json()["id"],
                "direction": "inbound",
                "author_type": "customer",
                "message_type": "text",
                "text": "Wrong identity",
            },
        )
        assert mismatched_identity_message.status_code == 404

        first_payload = {
            "external_identity_id": identity_id,
            "direction": "inbound",
            "author_type": "customer",
            "message_type": "text",
            "text": "Where is my order?",
            "external_message_id": "telegram-message-501",
            "idempotency_key": "telegram:account-1:event-501",
            "metadata": {"update_id": 501},
            "occurred_at": "2026-09-06T10:00:00Z",
            "attachments": [
                {
                    "media_type": "document",
                    "mime_type": "application/pdf",
                    "filename": "receipt.pdf",
                    "size_bytes": 1200,
                    "external_media_id": "file-501",
                    "metadata": {"provider_kind": "document"},
                }
            ],
        }
        first_message = client_a.post(
            f"/api/v1/tenants/{tenant_a}/conversations/{conversation_a_id}/messages",
            json=first_payload,
        )
        assert first_message.status_code == 201
        first_message_id = first_message.json()["id"]
        assert first_message.json()["attachments"][0]["filename"] == "receipt.pdf"

        retry_payload = dict(first_payload)
        retry_payload["text"] = "This retry must not overwrite original content"
        same_message = client_a.post(
            f"/api/v1/tenants/{tenant_a}/conversations/{conversation_a_id}/messages",
            json=retry_payload,
        )
        assert same_message.status_code == 200
        assert same_message.json()["id"] == first_message_id
        assert same_message.json()["text"] == "Where is my order?"

        second_conversation = create_conversation(client_a, tenant_a, contact_a_id)
        assert second_conversation.status_code == 201
        conflicting_key = client_a.post(
            f"/api/v1/tenants/{tenant_a}/conversations/{second_conversation.json()['id']}/messages",
            json=first_payload,
        )
        assert conflicting_key.status_code == 409

        earlier_message = client_a.post(
            f"/api/v1/tenants/{tenant_a}/conversations/{conversation_a_id}/messages",
            json={
                "external_identity_id": identity_id,
                "direction": "inbound",
                "author_type": "customer",
                "message_type": "text",
                "text": "Earlier message",
                "external_message_id": "telegram-message-500",
                "idempotency_key": "telegram:account-1:event-500",
                "occurred_at": "2026-09-06T09:00:00Z",
            },
        )
        assert earlier_message.status_code == 201

        history = client_a.get(
            f"/api/v1/tenants/{tenant_a}/conversations/{conversation_a_id}/messages"
        )
        assert history.status_code == 200
        assert [item["text"] for item in history.json()] == [
            "Earlier message",
            "Where is my order?",
        ]

        contact_b = create_contact(client_b, tenant_b, "Tenant B Customer")
        assert contact_b.status_code == 201
        conversation_b = create_conversation(client_b, tenant_b, contact_b.json()["id"])
        assert conversation_b.status_code == 201
        same_key_other_tenant = client_b.post(
            f"/api/v1/tenants/{tenant_b}/conversations/{conversation_b.json()['id']}/messages",
            json={
                "direction": "inbound",
                "author_type": "customer",
                "message_type": "text",
                "text": "Independent tenant event",
                "external_message_id": "different-provider-message",
                "idempotency_key": "telegram:account-1:event-501",
                "occurred_at": "2026-09-06T10:00:00Z",
            },
        )
        assert same_key_other_tenant.status_code == 201


def test_read_only_roles_can_read_but_cannot_write_customer_records() -> None:
    tenant_id, _ = seed_tenant_user(
        tenant_slug="rbac-tenant",
        tenant_name="RBAC Tenant",
        email="owner@example.com",
        password="owner password 1234",
    )
    seed_tenant_user(
        tenant_slug="unused-viewer",
        tenant_name="unused",
        email="viewer@example.com",
        password="viewer password 1234",
        role=TenantRole.VIEWER,
        tenant_id=tenant_id,
    )
    seed_tenant_user(
        tenant_slug="unused-analyst",
        tenant_name="unused",
        email="analyst@example.com",
        password="analyst password 1234",
        role=TenantRole.ANALYST,
        tenant_id=tenant_id,
    )

    with TestClient(create_app(TEST_SETTINGS)) as owner_client:
        login(owner_client, "owner@example.com", "owner password 1234")
        contact = create_contact(owner_client, tenant_id, "Readable Contact")
        assert contact.status_code == 201
        contact_id = contact.json()["id"]

    for email, password in (
        ("viewer@example.com", "viewer password 1234"),
        ("analyst@example.com", "analyst password 1234"),
    ):
        with TestClient(create_app(TEST_SETTINGS)) as read_only_client:
            login(read_only_client, email, password)
            listing = read_only_client.get(f"/api/v1/tenants/{tenant_id}/contacts")
            assert listing.status_code == 200
            assert [item["id"] for item in listing.json()] == [contact_id]
            assert create_contact(read_only_client, tenant_id, "Forbidden").status_code == 403
            assert create_conversation(read_only_client, tenant_id, contact_id).status_code == 403


def test_supervisor_and_agent_have_customer_domain_write_access() -> None:
    tenant_id, _ = seed_tenant_user(
        tenant_slug="staff-tenant",
        tenant_name="Staff Tenant",
        email="owner@example.com",
        password="owner password 1234",
    )
    seed_tenant_user(
        tenant_slug="unused-supervisor",
        tenant_name="unused",
        email="supervisor@example.com",
        password="supervisor password 1234",
        role=TenantRole.SUPERVISOR,
        tenant_id=tenant_id,
    )
    seed_tenant_user(
        tenant_slug="unused-agent",
        tenant_name="unused",
        email="agent@example.com",
        password="agent password 1234",
        role=TenantRole.AGENT,
        tenant_id=tenant_id,
    )

    with TestClient(create_app(TEST_SETTINGS)) as supervisor_client:
        login(supervisor_client, "supervisor@example.com", "supervisor password 1234")
        contact = create_contact(supervisor_client, tenant_id, "Created by supervisor")
        assert contact.status_code == 201
        contact_id = contact.json()["id"]

    with TestClient(create_app(TEST_SETTINGS)) as agent_client:
        login(agent_client, "agent@example.com", "agent password 1234")
        conversation = create_conversation(agent_client, tenant_id, contact_id)
        assert conversation.status_code == 201
        message = agent_client.post(
            f"/api/v1/tenants/{tenant_id}/conversations/{conversation.json()['id']}/messages",
            json={
                "direction": "internal",
                "author_type": "human",
                "message_type": "text",
                "text": "Internal note",
                "occurred_at": datetime(2026, 9, 6, 12, 0, tzinfo=UTC).isoformat(),
            },
        )
        assert message.status_code == 201


def test_sensitive_message_metadata_is_rejected_before_persistence() -> None:
    tenant_id, _ = seed_tenant_user(
        tenant_slug="metadata-tenant",
        tenant_name="Metadata Tenant",
        email="owner@example.com",
        password="owner password 1234",
    )

    with TestClient(create_app(TEST_SETTINGS)) as client:
        login(client, "owner@example.com", "owner password 1234")
        contact = create_contact(client, tenant_id, "Customer")
        conversation = create_conversation(client, tenant_id, contact.json()["id"])
        response = client.post(
            f"/api/v1/tenants/{tenant_id}/conversations/{conversation.json()['id']}/messages",
            json={
                "direction": "inbound",
                "author_type": "customer",
                "message_type": "text",
                "text": "hello",
                "metadata": {"nested": {"authorization": "Bearer secret"}},
            },
        )
        assert response.status_code == 422
