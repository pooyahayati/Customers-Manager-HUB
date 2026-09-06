import argparse
import asyncio
import getpass
import os
import re
from uuid import uuid7

from sqlalchemy import select

from customers_manager_hub.database import get_session_factory
from customers_manager_hub.models import AuditEvent, PlatformUser, Tenant, TenantMembership, TenantRole
from customers_manager_hub.security import hash_password, normalize_email

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def normalize_slug(value: str) -> str:
    """Normalize and validate a tenant slug used by the bootstrap command."""
    slug = value.strip().lower()
    if len(slug) > 100 or _SLUG_PATTERN.fullmatch(slug) is None:
        raise ValueError("tenant slug must contain lowercase letters, digits, and single hyphens")
    return slug


async def bootstrap_owner(email: str, password: str, tenant_name: str, tenant_slug: str) -> None:
    """Create the first owner and tenant in a clean installation."""
    normalized_email = normalize_email(email)
    normalized_slug = normalize_slug(tenant_slug)
    normalized_name = tenant_name.strip()
    if not normalized_name or len(normalized_name) > 200:
        raise ValueError("tenant name must contain between 1 and 200 characters")

    user_id = uuid7()
    tenant_id = uuid7()
    membership_id = uuid7()

    async with get_session_factory()() as db, db.begin():
        existing_user = (
            await db.execute(select(PlatformUser.id).where(PlatformUser.email == normalized_email))
        ).scalar_one_or_none()
        if existing_user is not None:
            raise ValueError("bootstrap user already exists")

        existing_tenant = (
            await db.execute(select(Tenant.id).where(Tenant.slug == normalized_slug))
        ).scalar_one_or_none()
        if existing_tenant is not None:
            raise ValueError("bootstrap tenant already exists")

        user = PlatformUser(
            id=user_id,
            email=normalized_email,
            password_hash=hash_password(password),
        )
        tenant = Tenant(id=tenant_id, name=normalized_name, slug=normalized_slug)
        membership = TenantMembership(
            id=membership_id,
            tenant_id=tenant_id,
            user_id=user_id,
            role=TenantRole.OWNER,
        )
        audit_event = AuditEvent(
            id=uuid7(),
            tenant_id=tenant_id,
            actor_user_id=user_id,
            action="bootstrap.owner_created",
            resource_type="tenant_membership",
            resource_id=str(membership_id),
            details={"role": TenantRole.OWNER.value},
        )
        db.add_all([user, tenant, membership, audit_event])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create the initial tenant owner")
    parser.add_argument("--email", required=True)
    parser.add_argument("--tenant-name", required=True)
    parser.add_argument("--tenant-slug", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    password = os.environ.get("CMH_BOOTSTRAP_PASSWORD") or getpass.getpass("Owner password: ")
    try:
        asyncio.run(
            bootstrap_owner(
                email=args.email,
                password=password,
                tenant_name=args.tenant_name,
                tenant_slug=args.tenant_slug,
            )
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print("Initial tenant owner created successfully.")


if __name__ == "__main__":
    main()
