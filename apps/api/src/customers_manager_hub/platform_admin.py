from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.billing_models import BusinessWallet
from customers_manager_hub.database import get_db_session
from customers_manager_hub.models import (
    AuditEvent,
    AuthSession,
    PlatformUser,
    Tenant,
    TenantMembership,
)
from customers_manager_hub.platform_auth import PlatformOwnerDependency
from customers_manager_hub.security import hash_password, normalize_email, normalize_slug

router = APIRouter(prefix="/api/v1/platform", tags=["platform"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]


class BusinessRole(StrEnum):
    ADMIN = "admin"
    SUPERVISOR = "supervisor"
    AGENT = "agent"
    VIEWER = "viewer"


class BusinessCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=63)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Business name must not be blank")
        return normalized

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        return normalize_slug(value)


class BusinessStatusUpdate(BaseModel):
    is_active: bool


class BusinessResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    is_active: bool
    user_count: int


class BusinessUserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=1024)
    role: BusinessRole = BusinessRole.AGENT

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class BusinessUserStatusUpdate(BaseModel):
    is_active: bool


class BusinessUserResponse(BaseModel):
    id: UUID
    email: str
    role: BusinessRole
    is_active: bool
    created_at: datetime


_BUSINESS_ROLE_VALUES = frozenset(role.value for role in BusinessRole)


def _business_response(tenant: Tenant, user_count: int) -> BusinessResponse:
    return BusinessResponse(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        is_active=tenant.is_active,
        user_count=user_count,
    )


async def _load_business(db: AsyncSession, business_id: UUID) -> Tenant:
    business = await db.get(Tenant, business_id)
    if business is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found")
    return business


@router.get("/businesses", response_model=list[BusinessResponse])
async def list_businesses(
    owner: PlatformOwnerDependency,
    db: DbSession,
) -> list[BusinessResponse]:
    del owner
    rows = (
        await db.execute(
            select(Tenant, func.count(PlatformUser.id))
            .outerjoin(TenantMembership, TenantMembership.tenant_id == Tenant.id)
            .outerjoin(
                PlatformUser,
                (PlatformUser.id == TenantMembership.user_id)
                & PlatformUser.is_platform_owner.is_(False),
            )
            .group_by(Tenant.id)
            .order_by(Tenant.created_at, Tenant.id)
        )
    ).all()
    return [_business_response(business, int(user_count)) for business, user_count in rows]


@router.post("/businesses", response_model=BusinessResponse, status_code=status.HTTP_201_CREATED)
async def create_business(
    payload: BusinessCreate,
    owner: PlatformOwnerDependency,
    db: DbSession,
) -> BusinessResponse:
    if await db.scalar(select(Tenant.id).where(Tenant.slug == payload.slug)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Business slug exists")
    business = Tenant(name=payload.name, slug=payload.slug)
    db.add(business)
    await db.flush()
    db.add(BusinessWallet(tenant_id=business.id))
    db.add(
        AuditEvent(
            tenant_id=business.id,
            actor_user_id=owner.id,
            action="platform.business.created",
            target_type="tenant",
            target_id=business.id,
            details={"slug": business.slug},
        )
    )
    await db.commit()
    await db.refresh(business)
    return _business_response(business, 0)


@router.patch("/businesses/{business_id}", response_model=BusinessResponse)
async def update_business_status(
    business_id: UUID,
    payload: BusinessStatusUpdate,
    owner: PlatformOwnerDependency,
    db: DbSession,
) -> BusinessResponse:
    business = await _load_business(db, business_id)
    business.is_active = payload.is_active
    if not payload.is_active:
        member_ids = select(TenantMembership.user_id).where(
            TenantMembership.tenant_id == business.id
        )
        await db.execute(
            update(AuthSession)
            .where(AuthSession.user_id.in_(member_ids), AuthSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
    db.add(
        AuditEvent(
            tenant_id=business.id,
            actor_user_id=owner.id,
            action="platform.business.status_updated",
            target_type="tenant",
            target_id=business.id,
            details={"is_active": payload.is_active},
        )
    )
    await db.commit()
    user_count = await db.scalar(
        select(func.count(PlatformUser.id))
        .join(TenantMembership, TenantMembership.user_id == PlatformUser.id)
        .where(
            TenantMembership.tenant_id == business.id,
            PlatformUser.is_platform_owner.is_(False),
        )
    )
    await db.refresh(business)
    return _business_response(business, int(user_count or 0))


@router.get("/businesses/{business_id}/users", response_model=list[BusinessUserResponse])
async def list_business_users(
    business_id: UUID,
    owner: PlatformOwnerDependency,
    db: DbSession,
) -> list[BusinessUserResponse]:
    del owner
    await _load_business(db, business_id)
    rows = (
        await db.execute(
            select(PlatformUser, TenantMembership)
            .join(TenantMembership, TenantMembership.user_id == PlatformUser.id)
            .where(
                TenantMembership.tenant_id == business_id,
                PlatformUser.is_platform_owner.is_(False),
            )
            .order_by(PlatformUser.created_at, PlatformUser.id)
        )
    ).all()
    return [
        BusinessUserResponse(
            id=user.id,
            email=user.email,
            role=BusinessRole(membership.role),
            is_active=membership.is_active and user.is_active,
            created_at=user.created_at,
        )
        for user, membership in rows
        if membership.role in _BUSINESS_ROLE_VALUES
    ]


@router.post(
    "/businesses/{business_id}/users",
    response_model=BusinessUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_business_user(
    business_id: UUID,
    payload: BusinessUserCreate,
    owner: PlatformOwnerDependency,
    db: DbSession,
) -> BusinessUserResponse:
    business = await _load_business(db, business_id)
    if not business.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Business is inactive")
    if await db.scalar(select(PlatformUser.id).where(PlatformUser.email == payload.email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")
    user = PlatformUser(email=payload.email, password_hash=hash_password(payload.password))
    db.add(user)
    await db.flush()
    membership = TenantMembership(
        tenant_id=business.id,
        user_id=user.id,
        role=payload.role.value,
    )
    db.add(membership)
    db.add(
        AuditEvent(
            tenant_id=business.id,
            actor_user_id=owner.id,
            action="platform.business_user.created",
            target_type="platform_user",
            target_id=user.id,
            details={"role": payload.role.value},
        )
    )
    await db.commit()
    await db.refresh(user)
    return BusinessUserResponse(
        id=user.id,
        email=user.email,
        role=payload.role,
        is_active=True,
        created_at=user.created_at,
    )


@router.patch(
    "/businesses/{business_id}/users/{user_id}",
    response_model=BusinessUserResponse,
)
async def update_business_user_status(
    business_id: UUID,
    user_id: UUID,
    payload: BusinessUserStatusUpdate,
    owner: PlatformOwnerDependency,
    db: DbSession,
) -> BusinessUserResponse:
    row = (
        await db.execute(
            select(PlatformUser, TenantMembership)
            .join(TenantMembership, TenantMembership.user_id == PlatformUser.id)
            .where(
                PlatformUser.id == user_id,
                PlatformUser.is_platform_owner.is_(False),
                TenantMembership.tenant_id == business_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business user not found")
    user, membership = row
    if membership.role not in _BUSINESS_ROLE_VALUES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Unsupported legacy role")
    membership.is_active = payload.is_active
    if not payload.is_active:
        await db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
    db.add(
        AuditEvent(
            tenant_id=business_id,
            actor_user_id=owner.id,
            action="platform.business_user.status_updated",
            target_type="platform_user",
            target_id=user.id,
            details={"is_active": payload.is_active},
        )
    )
    await db.commit()
    await db.refresh(user)
    return BusinessUserResponse(
        id=user.id,
        email=user.email,
        role=BusinessRole(membership.role),
        is_active=membership.is_active and user.is_active,
        created_at=user.created_at,
    )
