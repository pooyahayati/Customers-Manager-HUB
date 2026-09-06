from enum import StrEnum

from customers_manager_hub.models import TenantRole


class Permission(StrEnum):
    TENANT_READ = "tenant.read"
    TENANT_MANAGE = "tenant.manage"
    MEMBERSHIP_READ = "membership.read"
    MEMBERSHIP_MANAGE = "membership.manage"
    AUDIT_READ = "audit.read"


ROLE_PERMISSIONS: dict[TenantRole, frozenset[Permission]] = {
    TenantRole.OWNER: frozenset(Permission),
    TenantRole.ADMIN: frozenset(Permission),
    TenantRole.SUPERVISOR: frozenset(
        {
            Permission.TENANT_READ,
            Permission.MEMBERSHIP_READ,
            Permission.AUDIT_READ,
        }
    ),
    TenantRole.AGENT: frozenset({Permission.TENANT_READ}),
    TenantRole.ANALYST: frozenset({Permission.TENANT_READ, Permission.AUDIT_READ}),
    TenantRole.VIEWER: frozenset({Permission.TENANT_READ}),
}


def has_permission(role: TenantRole, permission: Permission) -> bool:
    """Evaluate one fixed MVP role against a server-side permission."""
    return permission in ROLE_PERMISSIONS[role]
