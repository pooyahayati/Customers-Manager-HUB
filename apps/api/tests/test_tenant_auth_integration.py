import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from customers_manager_hub.auth import SESSION_COOKIE_NAME
from customers_manager_hub.bootstrap import BootstrapError, bootstrap_initial_owner
from customers_manager_hub.config import Settings
from customers_manager_hub.main import create_app
from customers_manager_hub.models import (
    AuditEvent,
    AuthSession,
    PlatformUser,
    Tenant,
    TenantMembership,
    TenantRole,
)
from customers_manager_hub.security import hash_password, hash_session_token

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


def client_get(client: TestClient, path: str) -> Response:
    return client.get(path)


def client_post(
    client: TestClient,
    path: str,
    *,
    json: dict[str, str] | None = None,
) -> Response:
    return client.post(path, json=json)


def client_patch(client: TestClient, path: str, *, json: dict[str, str]) -> Response:
    return client.patch(path, json=json)


@pytest.fixture(autouse=True)
def clean_identity_tables() -> Iterator[None]:
    with SYNC_ENGINE.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE audit_events, auth_sessions, tenant_memberships, "
                "platform_users, tenants CASCADE"
            )
        )
    yield
    with SYNC_ENGINE.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE audit_events, auth_sessions, tenant_memberships, "
                "platform_users, tenants CASCADE"
            )
        )


def bootstrap_owner() -> tuple[UUID, UUID]:
    return asyncio.run(
        bootstrap_initial_owner(
            tenant_name="Acme Store",
            tenant_slug="acme-store",
            email="owner@example.com",
            password="correct horse battery staple",
            settings=TEST_SETTINGS,
        )
    )


def create_unrelated_tenant() -> UUID:
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        tenant = Tenant(name="Other Business", slug="other-business")
        db.add(tenant)
        db.commit()
        return tenant.id


def create_viewer(tenant_id: UUID) -> UUID:
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        user = PlatformUser(
            email="viewer@example.com",
            password_hash=hash_password("viewer password 1234"),
        )
        db.add(user)
        db.flush()
        db.add(
            TenantMembership(
                tenant_id=tenant_id,
                user_id=user.id,
                role=TenantRole.VIEWER.value,
            )
        )
        db.commit()
        return user.id


def test_bootstrap_is_single_use_and_audited() -> None:
    tenant_id, owner_id = bootstrap_owner()

    with Session(SYNC_ENGINE) as db:
        owner = db.get(PlatformUser, owner_id)
        audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "tenant.bootstrap"))
        assert owner is not None
        assert owner.password_hash != "correct horse battery staple"
        assert audit is not None
        assert audit.tenant_id == tenant_id
        assert audit.actor_user_id == owner_id

    with pytest.raises(BootstrapError):
        bootstrap_owner()


def test_login_tenant_isolation_rbac_audit_and_logout() -> None:
    tenant_id, owner_id = bootstrap_owner()
    other_tenant_id = create_unrelated_tenant()
    viewer_id = create_viewer(tenant_id)

    with TestClient(create_app(TEST_SETTINGS)) as owner_client:
        invalid = client_post(
            owner_client,
            "/api/v1/auth/login",
            json={"email": "owner@example.com", "password": "wrong password"},
        )
        assert invalid.status_code == 401
        assert invalid.json()["detail"] == "Invalid email or password"

        login = client_post(
            owner_client,
            "/api/v1/auth/login",
            json={
                "email": " OWNER@example.com ",
                "password": "correct horse battery staple",
            },
        )
        assert login.status_code == 200
        set_cookie = login.headers["set-cookie"].lower()
        assert "httponly" in set_cookie
        assert "samesite=strict" in set_cookie

        raw_token = login.cookies.get(SESSION_COOKIE_NAME)
        assert raw_token is not None
        with Session(SYNC_ENGINE) as db:
            stored_session = db.scalar(
                select(AuthSession).where(AuthSession.token_hash == hash_session_token(raw_token))
            )
            assert stored_session is not None
            assert stored_session.token_hash != raw_token
            assert stored_session.user_id == owner_id

        me = client_get(owner_client, "/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == "owner@example.com"

        tenants = client_get(owner_client, "/api/v1/tenants")
        assert tenants.status_code == 200
        assert [item["id"] for item in tenants.json()] == [str(tenant_id)]

        own_tenant = client_get(owner_client, f"/api/v1/tenants/{tenant_id}")
        assert own_tenant.status_code == 200
        assert own_tenant.json()["role"] == "owner"

        cross_tenant = client_get(owner_client, f"/api/v1/tenants/{other_tenant_id}")
        assert cross_tenant.status_code == 404

        updated = client_patch(
            owner_client,
            f"/api/v1/tenants/{tenant_id}",
            json={"name": "Acme Store Updated"},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Acme Store Updated"

        with Session(SYNC_ENGINE) as db:
            audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "tenant.updated"))
            assert audit is not None
            assert audit.tenant_id == tenant_id
            assert audit.actor_user_id == owner_id
            assert audit.details == {"changed_fields": ["name"]}

        with TestClient(create_app(TEST_SETTINGS)) as viewer_client:
            viewer_login = client_post(
                viewer_client,
                "/api/v1/auth/login",
                json={"email": "viewer@example.com", "password": "viewer password 1234"},
            )
            assert viewer_login.status_code == 200

            forbidden = client_patch(
                viewer_client,
                f"/api/v1/tenants/{tenant_id}",
                json={"name": "Viewer Must Not Change This"},
            )
            assert forbidden.status_code == 403

            with Session(SYNC_ENGINE) as db:
                membership = db.scalar(
                    select(TenantMembership).where(
                        TenantMembership.tenant_id == tenant_id,
                        TenantMembership.user_id == viewer_id,
                    )
                )
                assert membership is not None
                membership.is_active = False
                db.commit()

            inactive_membership = client_get(viewer_client, f"/api/v1/tenants/{tenant_id}")
            assert inactive_membership.status_code == 404

        logout = client_post(owner_client, "/api/v1/auth/logout")
        assert logout.status_code == 204
        assert client_get(owner_client, "/api/v1/auth/me").status_code == 401

        with Session(SYNC_ENGINE) as db:
            revoked_session = db.scalar(
                select(AuthSession).where(AuthSession.token_hash == hash_session_token(raw_token))
            )
            assert revoked_session is not None
            assert revoked_session.revoked_at is not None


def test_expired_session_is_rejected() -> None:
    bootstrap_owner()

    with TestClient(create_app(TEST_SETTINGS)) as client:
        login = client_post(
            client,
            "/api/v1/auth/login",
            json={
                "email": "owner@example.com",
                "password": "correct horse battery staple",
            },
        )
        assert login.status_code == 200
        raw_token = login.cookies.get(SESSION_COOKIE_NAME)
        assert raw_token is not None

        with Session(SYNC_ENGINE) as db:
            auth_session = db.scalar(
                select(AuthSession).where(AuthSession.token_hash == hash_session_token(raw_token))
            )
            assert auth_session is not None
            auth_session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            db.commit()

        assert client_get(client, "/api/v1/auth/me").status_code == 401
