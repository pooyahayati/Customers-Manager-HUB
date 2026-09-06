import argparse
import asyncio
import getpass
from uuid import UUID

from sqlalchemy import func, select

from customers_manager_hub.config import Settings, get_settings
from customers_manager_hub.database import create_database
from customers_manager_hub.models import (
    AuditEvent,
    PlatformUser,
    Tenant,
    TenantMembership,
    TenantRole,
)
from customers_manager_hub.security import hash_password, normalize_email, normalize_slug


class BootstrapError(RuntimeError):
    pass


async def bootstrap_initial_owner(
    *,
    tenant_name: str,
    tenant_slug: str,
    email: str,
    password: str,
    settings: Settings | None = None,
) -> tuple[UUID, UUID]:
    """Create the first tenant and owner only on an empty identity database."""
    resolved_settings = settings or get_settings()
    normalized_name = tenant_name.strip()
    if not normalized_name or len(normalized_name) > 200:
        raise ValueError("Tenant name must contain 1 to 200 characters")

    normalized_slug = normalize_slug(tenant_slug)
    normalized_email = normalize_email(email)
    encoded_password = hash_password(password)

    engine, session_factory = create_database(resolved_settings)
    try:
        async with session_factory() as db, db.begin():
            tenant_count = await db.scalar(select(func.count()).select_from(Tenant))
            user_count = await db.scalar(select(func.count()).select_from(PlatformUser))
            if (tenant_count or 0) != 0 or (user_count or 0) != 0:
                raise BootstrapError(
                    "Initial bootstrap is allowed only on an empty identity database"
                )

            tenant = Tenant(slug=normalized_slug, name=normalized_name)
            user = PlatformUser(email=normalized_email, password_hash=encoded_password)
            db.add_all([tenant, user])
            await db.flush()

            db.add(
                TenantMembership(
                    tenant_id=tenant.id,
                    user_id=user.id,
                    role=TenantRole.OWNER.value,
                )
            )
            db.add(
                AuditEvent(
                    tenant_id=tenant.id,
                    actor_user_id=user.id,
                    action="tenant.bootstrap",
                    target_type="tenant",
                    target_id=tenant.id,
                    details={"role": TenantRole.OWNER.value},
                )
            )
            return tenant.id, user.id
    finally:
        await engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create the initial tenant owner")
    parser.add_argument("--tenant-name", required=True)
    parser.add_argument("--tenant-slug", required=True)
    parser.add_argument("--email", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")

    try:
        tenant_id, user_id = asyncio.run(
            bootstrap_initial_owner(
                tenant_name=args.tenant_name,
                tenant_slug=args.tenant_slug,
                email=args.email,
                password=password,
            )
        )
    except (BootstrapError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Created tenant {tenant_id} and owner {user_id}")


if __name__ == "__main__":
    main()
