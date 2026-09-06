from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.auth import CurrentAuth, CurrentAuthDependency
from customers_manager_hub.database import get_db_session
from customers_manager_hub.models import AuditEvent, Tenant, TenantMembership, TenantRole

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]


class TenantResponse(BaseModel):
    id: UUID
    slug: str
    name: str
    role: TenantRole


class TenantUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Tenant name must not be blank")
        return normalized


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant: Tenant
    membership: TenantMembership
    current: CurrentAuth


async def get_tenant_context(
    tenant_id: UUID,
    current: CurrentAuthDependency,
    db: DbSession,
) -> TenantContext:
    statement = (
        select(Tenant, TenantMembership)
        .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
        .where(
            Tenant.id == tenant_id,
            Tenant.is_active.is_(True),
            TenantMembership.user_id == current.user.id,
            TenantMembership.is_active.is_(True),
        )
    )
    row = (await db.execute(statement)).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    tenant, membership = row
    return TenantContext(tenant=tenant, membership=membership, current=current)


TenantContextDependency = Annotated[TenantContext, Depends(get_tenant_context)]


def tenant_response(context: TenantContext) -> TenantResponse:
    return TenantResponse(
        id=context.tenant.id,
        slug=context.tenant.slug,
        name=context.tenant.name,
        role=TenantRole(context.membership.role),
    )


@router.get("", response_model=list[TenantResponse])
async def list_tenants(current: CurrentAuthDependency, db: DbSession) -> list[TenantResponse]:
    statement = (
        select(Tenant, TenantMembership)
        .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
        .where(
            TenantMembership.user_id == current.user.id,
            TenantMembership.is_active.is_(True),
            Tenant.is_active.is_(True),
        )
        .order_by(Tenant.name, Tenant.id)
    )
    rows = (await db.execute(statement)).all()
    return [
        TenantResponse(
            id=tenant.id,
            slug=tenant.slug,
            name=tenant.name,
            role=TenantRole(membership.role),
        )
        for tenant, membership in rows
    ]


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(context: TenantContextDependency) -> TenantResponse:
    return tenant_response(context)


@router.patch("/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    payload: TenantUpdate,
    context: TenantContextDependency,
    db: DbSession,
) -> TenantResponse:
    role = TenantRole(context.membership.role)
    if role not in {TenantRole.OWNER, TenantRole.ADMIN}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permission")

    if context.tenant.name != payload.name:
        context.tenant.name = payload.name
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="tenant.updated",
                target_type="tenant",
                target_id=context.tenant.id,
                details={"changed_fields": ["name"]},
            )
        )
        await db.commit()
        await db.refresh(context.tenant)

    return tenant_response(context)
