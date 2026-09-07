from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.contacts import load_contact
from customers_manager_hub.database import get_db_session
from customers_manager_hub.memory_models import (
    CustomerMemoryItem,
    MemoryCategory,
    MemoryEvidenceKind,
    MemorySourceType,
)
from customers_manager_hub.memory_runtime import (
    MemoryRuntimeError,
    _candidate_dedupe_key,
    _normalize_value,
)
from customers_manager_hub.models import AuditEvent, TenantRole
from customers_manager_hub.tenants import TenantContextDependency, require_tenant_role

router = APIRouter(
    prefix="/api/v1/tenants/{tenant_id}/contacts/{contact_id}/memories",
    tags=["customer-memory"],
)
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
WriteRoles = frozenset(
    {TenantRole.OWNER, TenantRole.ADMIN, TenantRole.SUPERVISOR, TenantRole.AGENT}
)


class MemoryCreate(BaseModel):
    category: MemoryCategory
    value: str = Field(min_length=1, max_length=1_000)
    evidence_kind: MemoryEvidenceKind = MemoryEvidenceKind.FACT
    confidence: float = Field(default=1.0, ge=0, le=1)
    expires_at: datetime | None = None


class MemoryUpdate(BaseModel):
    value: str | None = Field(default=None, min_length=1, max_length=1_000)
    evidence_kind: MemoryEvidenceKind | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    expires_at: datetime | None = None
    set_expires_at: bool = False
    verified: bool | None = None


class MemoryResponse(BaseModel):
    id: UUID
    contact_id: UUID
    category: MemoryCategory
    value: str
    evidence_kind: MemoryEvidenceKind
    confidence: float
    source_type: MemorySourceType
    source_message_id: UUID | None
    source_conversation_id: UUID | None
    created_by_user_id: UUID | None
    observed_at: datetime
    expires_at: datetime | None
    verified_at: datetime | None
    verified_by_user_id: UUID | None
    deleted_at: datetime | None
    deleted_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


def memory_response(item: CustomerMemoryItem) -> MemoryResponse:
    return MemoryResponse(
        id=item.id,
        contact_id=item.contact_id,
        category=MemoryCategory(item.category),
        value=item.value,
        evidence_kind=MemoryEvidenceKind(item.evidence_kind),
        confidence=item.confidence,
        source_type=MemorySourceType(item.source_type),
        source_message_id=item.source_message_id,
        source_conversation_id=item.source_conversation_id,
        created_by_user_id=item.created_by_user_id,
        observed_at=item.observed_at,
        expires_at=item.expires_at,
        verified_at=item.verified_at,
        verified_by_user_id=item.verified_by_user_id,
        deleted_at=item.deleted_at,
        deleted_by_user_id=item.deleted_by_user_id,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


async def _load_memory(
    db: AsyncSession,
    tenant_id: UUID,
    contact_id: UUID,
    memory_id: UUID,
) -> CustomerMemoryItem:
    item = await db.scalar(
        select(CustomerMemoryItem).where(
            CustomerMemoryItem.id == memory_id,
            CustomerMemoryItem.tenant_id == tenant_id,
            CustomerMemoryItem.contact_id == contact_id,
        )
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory item not found")
    return item


@router.get("", response_model=list[MemoryResponse])
async def list_customer_memories(
    contact_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
    include_deleted: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[MemoryResponse]:
    await load_contact(db, context.tenant.id, contact_id)
    if include_deleted:
        require_tenant_role(context, WriteRoles)
    statement = select(CustomerMemoryItem).where(
        CustomerMemoryItem.tenant_id == context.tenant.id,
        CustomerMemoryItem.contact_id == contact_id,
    )
    if not include_deleted:
        statement = statement.where(CustomerMemoryItem.deleted_at.is_(None))
    items = list(
        (
            await db.scalars(
                statement.order_by(
                    CustomerMemoryItem.deleted_at.asc().nullsfirst(),
                    CustomerMemoryItem.updated_at.desc(),
                    CustomerMemoryItem.id,
                ).limit(limit)
            )
        ).all()
    )
    return [memory_response(item) for item in items]


@router.post("", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_customer_memory(
    contact_id: UUID,
    payload: MemoryCreate,
    context: TenantContextDependency,
    db: DbSession,
) -> MemoryResponse:
    require_tenant_role(context, WriteRoles)
    await load_contact(db, context.tenant.id, contact_id)
    now = datetime.now(UTC)
    try:
        value = _normalize_value(payload.category, payload.value)
    except MemoryRuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.code,
        ) from exc
    if payload.expires_at is not None and payload.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="expires_at must be in the future",
        )
    item = CustomerMemoryItem(
        tenant_id=context.tenant.id,
        contact_id=contact_id,
        category=payload.category.value,
        value=value,
        dedupe_key=_candidate_dedupe_key(payload.category, value),
        evidence_kind=payload.evidence_kind.value,
        confidence=payload.confidence,
        source_type=MemorySourceType.MANUAL.value,
        created_by_user_id=context.current.user.id,
        observed_at=now,
        expires_at=payload.expires_at,
        verified_at=now,
        verified_by_user_id=context.current.user.id,
    )
    db.add(item)
    await db.flush()
    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="customer_memory.created",
            target_type="customer_memory_item",
            target_id=item.id,
            details={"contact_id": str(contact_id), "category": payload.category.value},
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An active memory item already exists for this category/value",
        ) from exc
    await db.refresh(item)
    return memory_response(item)


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def update_customer_memory(
    contact_id: UUID,
    memory_id: UUID,
    payload: MemoryUpdate,
    context: TenantContextDependency,
    db: DbSession,
) -> MemoryResponse:
    require_tenant_role(context, WriteRoles)
    await load_contact(db, context.tenant.id, contact_id)
    item = await _load_memory(db, context.tenant.id, contact_id, memory_id)
    if item.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Memory item is deleted")

    now = datetime.now(UTC)
    changed = False
    if payload.value is not None:
        category = MemoryCategory(item.category)
        try:
            value = _normalize_value(category, payload.value)
        except MemoryRuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=exc.code,
            ) from exc
        item.value = value
        item.dedupe_key = _candidate_dedupe_key(category, value)
        changed = True
    if payload.evidence_kind is not None:
        item.evidence_kind = payload.evidence_kind.value
        changed = True
    if payload.confidence is not None:
        item.confidence = payload.confidence
        changed = True
    if payload.set_expires_at:
        if payload.expires_at is not None and payload.expires_at <= now:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="expires_at must be in the future",
            )
        item.expires_at = payload.expires_at
        changed = True
    if payload.verified is not None:
        if payload.verified:
            item.verified_at = now
            item.verified_by_user_id = context.current.user.id
        else:
            item.verified_at = None
            item.verified_by_user_id = None
        changed = True

    if changed:
        item.source_type = MemorySourceType.MANUAL.value
        item.created_by_user_id = context.current.user.id
        item.source_message_id = None
        item.source_conversation_id = None
        item.observed_at = now
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="customer_memory.updated",
                target_type="customer_memory_item",
                target_id=item.id,
                details={"contact_id": str(contact_id), "category": item.category},
            )
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An active memory item already exists for this category/value",
        ) from exc
    await db.refresh(item)
    return memory_response(item)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer_memory(
    contact_id: UUID,
    memory_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> Response:
    require_tenant_role(context, WriteRoles)
    await load_contact(db, context.tenant.id, contact_id)
    item = await _load_memory(db, context.tenant.id, contact_id, memory_id)
    if item.deleted_at is None:
        item.deleted_at = datetime.now(UTC)
        item.deleted_by_user_id = context.current.user.id
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="customer_memory.deleted",
                target_type="customer_memory_item",
                target_id=item.id,
                details={"contact_id": str(contact_id), "category": item.category},
            )
        )
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
