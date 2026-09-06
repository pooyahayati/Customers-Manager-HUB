from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.auth import AuthContext, get_auth_context
from customers_manager_hub.authorization import Permission, has_permission
from customers_manager_hub.database import get_db_session
from customers_manager_hub.models import Tenant, TenantMembership, TenantRole

router = APIRouter(prefix="/tenants", tags=["tenants"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


class TenantResponse(BaseModel):
    id: str
    name: str
    slug: str
    role: TenantRole


def tenant_response(tenant: Tenant, role: TenantRole) -> TenantResponse:
    return TenantResponse(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        role=role,
    )


@router.get("", response_model=list[TenantResponse])
async def list_tenants(auth: CurrentAuth, db: DbSession) -> list[TenantResponse]:
    """List only active tenants available to the authenticated user."""
    statement = (
        select(Tenant, TenantMembership.role)
        .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
        .where(
            TenantMembership.user_id == auth.user.id,
            TenantMembership.is_active.is_(True),
            Tenant.is_active.is_(True),
        )
        .order_by(Tenant.name, Tenant.id)
    )
    rows = (await db.execute(statement)).all()
    return [
        tenant_response(tenant, role)
        for tenant, role in rows
        if has_permission(role, Permission.TENANT_READ)
    ]


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(tenant_id: UUID, auth: CurrentAuth, db: DbSession) -> TenantResponse:
    """Resolve a tenant only through the authenticated user's active membership."""
    statement = (
        select(Tenant, TenantMembership.role)
        .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
        .where(
            Tenant.id == tenant_id,
            TenantMembership.user_id == auth.user.id,
            TenantMembership.is_active.is_(True),
            Tenant.is_active.is_(True),
        )
    )
    row = (await db.execute(statement)).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    tenant, role = row
    if not has_permission(role, Permission.TENANT_READ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant_response(tenant, role)
