from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.database import get_db_session
from customers_manager_hub.models import Contact, ExternalIdentity, TenantRole
from customers_manager_hub.tenants import (
    TenantContextDependency,
    require_tenant_role,
)

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/contacts", tags=["contacts"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
WriteRole = frozenset({TenantRole.OWNER, TenantRole.ADMIN, TenantRole.SUPERVISOR, TenantRole.AGENT})


class ContactCreate(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ContactResponse(BaseModel):
    id: UUID
    display_name: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ExternalIdentityCreate(BaseModel):
    namespace: str = Field(min_length=1, max_length=100)
    external_id: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=200)

    @field_validator("namespace")
    @classmethod
    def normalize_namespace(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("Identity namespace must not be blank")
        return normalized

    @field_validator("external_id")
    @classmethod
    def normalize_external_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("External identity must not be blank")
        return normalized

    @field_validator("display_name")
    @classmethod
    def normalize_identity_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ExternalIdentityResponse(BaseModel):
    id: UUID
    contact_id: UUID
    namespace: str
    external_id: str
    display_name: str | None
    created_at: datetime
    updated_at: datetime


class ContactDetailResponse(ContactResponse):
    identities: list[ExternalIdentityResponse]


def contact_response(contact: Contact) -> ContactResponse:
    return ContactResponse(
        id=contact.id,
        display_name=contact.display_name,
        is_active=contact.is_active,
        created_at=contact.created_at,
        updated_at=contact.updated_at,
    )


def identity_response(identity: ExternalIdentity) -> ExternalIdentityResponse:
    return ExternalIdentityResponse(
        id=identity.id,
        contact_id=identity.contact_id,
        namespace=identity.namespace,
        external_id=identity.external_id,
        display_name=identity.display_name,
        created_at=identity.created_at,
        updated_at=identity.updated_at,
    )


async def load_contact(db: AsyncSession, tenant_id: UUID, contact_id: UUID) -> Contact:
    contact = await db.scalar(
        select(Contact).where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
    )
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    return contact


async def load_identity_by_key(
    db: AsyncSession,
    tenant_id: UUID,
    namespace: str,
    external_id: str,
) -> ExternalIdentity | None:
    return await db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.tenant_id == tenant_id,
            ExternalIdentity.namespace == namespace,
            ExternalIdentity.external_id == external_id,
        )
    )


def resolve_identity_conflict(identity: ExternalIdentity, contact_id: UUID) -> ExternalIdentity:
    if identity.contact_id != contact_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="External identity is already assigned to another contact",
        )
    return identity


@router.post("", response_model=ContactResponse)
async def create_contact(
    payload: ContactCreate,
    response: Response,
    context: TenantContextDependency,
    db: DbSession,
) -> ContactResponse:
    require_tenant_role(context, WriteRole)
    contact = Contact(tenant_id=context.tenant.id, display_name=payload.display_name)
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    response.status_code = status.HTTP_201_CREATED
    return contact_response(contact)


@router.get("", response_model=list[ContactResponse])
async def list_contacts(
    context: TenantContextDependency,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ContactResponse]:
    contacts = (
        await db.scalars(
            select(Contact)
            .where(Contact.tenant_id == context.tenant.id)
            .order_by(Contact.created_at.desc(), Contact.id)
            .limit(limit)
        )
    ).all()
    return [contact_response(contact) for contact in contacts]


@router.get("/{contact_id}", response_model=ContactDetailResponse)
async def get_contact(
    contact_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> ContactDetailResponse:
    contact = await load_contact(db, context.tenant.id, contact_id)
    identities = (
        await db.scalars(
            select(ExternalIdentity)
            .where(
                ExternalIdentity.tenant_id == context.tenant.id,
                ExternalIdentity.contact_id == contact.id,
            )
            .order_by(ExternalIdentity.namespace, ExternalIdentity.external_id, ExternalIdentity.id)
        )
    ).all()
    return ContactDetailResponse(
        **contact_response(contact).model_dump(),
        identities=[identity_response(identity) for identity in identities],
    )


@router.post("/{contact_id}/identities", response_model=ExternalIdentityResponse)
async def add_external_identity(
    contact_id: UUID,
    payload: ExternalIdentityCreate,
    response: Response,
    context: TenantContextDependency,
    db: DbSession,
) -> ExternalIdentityResponse:
    require_tenant_role(context, WriteRole)
    contact = await load_contact(db, context.tenant.id, contact_id)

    existing = await load_identity_by_key(
        db,
        context.tenant.id,
        payload.namespace,
        payload.external_id,
    )
    if existing is not None:
        return identity_response(resolve_identity_conflict(existing, contact.id))

    identity = ExternalIdentity(
        tenant_id=context.tenant.id,
        contact_id=contact.id,
        namespace=payload.namespace,
        external_id=payload.external_id,
        display_name=payload.display_name,
    )
    db.add(identity)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raced_identity = await load_identity_by_key(
            db,
            context.tenant.id,
            payload.namespace,
            payload.external_id,
        )
        if raced_identity is None:
            raise
        return identity_response(resolve_identity_conflict(raced_identity, contact.id))

    await db.refresh(identity)
    response.status_code = status.HTTP_201_CREATED
    return identity_response(identity)
