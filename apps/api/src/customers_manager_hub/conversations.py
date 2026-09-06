import json
from datetime import UTC, datetime
from typing import Annotated, Self, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.database import get_db_session
from customers_manager_hub.models import (
    Contact,
    Conversation,
    ConversationStatus,
    ExternalIdentity,
    Message,
    MessageAttachment,
    MessageAuthorType,
    MessageDirection,
    MessageType,
    TenantRole,
)
from customers_manager_hub.tenants import TenantContextDependency, require_tenant_role

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/conversations", tags=["conversations"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
WriteRole = frozenset({TenantRole.OWNER, TenantRole.ADMIN, TenantRole.SUPERVISOR, TenantRole.AGENT})
_METADATA_LIMIT_BYTES = 16 * 1024
_SENSITIVE_METADATA_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "passwd",
    "refresh_token",
    "secret",
    "session",
    "token",
    "access_token",
}


def validate_safe_metadata(value: dict[str, object]) -> dict[str, object]:
    def visit(node: object) -> None:
        if isinstance(node, dict):
            mapping = cast(dict[object, object], node)
            for raw_key, child in mapping.items():
                if not isinstance(raw_key, str):
                    raise ValueError("Metadata keys must be strings")
                if raw_key.strip().casefold() in _SENSITIVE_METADATA_KEYS:
                    raise ValueError(f"Sensitive metadata key is not allowed: {raw_key}")
                visit(child)
        elif isinstance(node, list):
            for child in cast(list[object], node):
                visit(child)

    visit(value)
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _METADATA_LIMIT_BYTES:
        raise ValueError("Metadata object is too large")
    return value


class ConversationCreate(BaseModel):
    contact_id: UUID
    subject: str | None = Field(default=None, max_length=300)

    @field_validator("subject")
    @classmethod
    def normalize_subject(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ConversationResponse(BaseModel):
    id: UUID
    contact_id: UUID
    status: ConversationStatus
    subject: str | None
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MessageAttachmentCreate(BaseModel):
    media_type: str = Field(min_length=1, max_length=32)
    mime_type: str | None = Field(default=None, max_length=255)
    filename: str | None = Field(default=None, max_length=500)
    size_bytes: int | None = Field(default=None, ge=0)
    external_media_id: str | None = Field(default=None, max_length=1024)
    storage_key: str | None = Field(default=None, max_length=1024)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("media_type")
    @classmethod
    def normalize_media_type(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("Media type must not be blank")
        return normalized

    @field_validator("mime_type", "filename", "external_media_id", "storage_key")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_safe_metadata(value)


def empty_attachment_payloads() -> list[MessageAttachmentCreate]:
    return []


class MessageCreate(BaseModel):
    external_identity_id: UUID | None = None
    direction: MessageDirection
    author_type: MessageAuthorType
    message_type: MessageType
    text: str | None = Field(default=None, max_length=100_000)
    external_message_id: str | None = Field(default=None, max_length=255)
    idempotency_key: str | None = Field(default=None, max_length=255)
    metadata: dict[str, object] = Field(default_factory=dict)
    occurred_at: datetime | None = None
    attachments: list[MessageAttachmentCreate] = Field(
        default_factory=empty_attachment_payloads,
        max_length=16,
    )

    @field_validator("external_message_id", "idempotency_key")
    @classmethod
    def normalize_optional_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("occurred_at")
    @classmethod
    def normalize_occurred_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Message occurrence timestamp must include a timezone")
        return value.astimezone(UTC)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_safe_metadata(value)

    @model_validator(mode="after")
    def validate_text_message(self) -> Self:
        if self.message_type == MessageType.TEXT and (self.text is None or not self.text.strip()):
            raise ValueError("Text messages require non-blank text")
        return self


class MessageAttachmentResponse(BaseModel):
    id: UUID
    media_type: str
    mime_type: str | None
    filename: str | None
    size_bytes: int | None
    external_media_id: str | None
    storage_key: str | None
    metadata: dict[str, object]
    created_at: datetime


class MessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    external_identity_id: UUID | None
    direction: MessageDirection
    author_type: MessageAuthorType
    message_type: MessageType
    text: str | None
    external_message_id: str | None
    idempotency_key: str | None
    metadata: dict[str, object]
    occurred_at: datetime
    created_at: datetime
    attachments: list[MessageAttachmentResponse]


def conversation_response(conversation: Conversation) -> ConversationResponse:
    return ConversationResponse(
        id=conversation.id,
        contact_id=conversation.contact_id,
        status=ConversationStatus(conversation.status),
        subject=conversation.subject,
        last_message_at=conversation.last_message_at,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def attachment_response(attachment: MessageAttachment) -> MessageAttachmentResponse:
    return MessageAttachmentResponse(
        id=attachment.id,
        media_type=attachment.media_type,
        mime_type=attachment.mime_type,
        filename=attachment.filename,
        size_bytes=attachment.size_bytes,
        external_media_id=attachment.external_media_id,
        storage_key=attachment.storage_key,
        metadata=attachment.media_metadata,
        created_at=attachment.created_at,
    )


def message_response(
    message: Message,
    attachments: list[MessageAttachment],
) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        external_identity_id=message.external_identity_id,
        direction=MessageDirection(message.direction),
        author_type=MessageAuthorType(message.author_type),
        message_type=MessageType(message.message_type),
        text=message.text,
        external_message_id=message.external_message_id,
        idempotency_key=message.idempotency_key,
        metadata=message.external_metadata,
        occurred_at=message.occurred_at,
        created_at=message.created_at,
        attachments=[attachment_response(attachment) for attachment in attachments],
    )


async def load_contact(db: AsyncSession, tenant_id: UUID, contact_id: UUID) -> Contact:
    contact = await db.scalar(
        select(Contact).where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
    )
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    return contact


async def load_conversation(
    db: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
) -> Conversation:
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == tenant_id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


async def validate_external_identity(
    db: AsyncSession,
    tenant_id: UUID,
    contact_id: UUID,
    external_identity_id: UUID | None,
) -> None:
    if external_identity_id is None:
        return
    identity = await db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.id == external_identity_id,
            ExternalIdentity.tenant_id == tenant_id,
            ExternalIdentity.contact_id == contact_id,
        )
    )
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="External identity not found for conversation contact",
        )


async def load_message_by_idempotency_key(
    db: AsyncSession,
    tenant_id: UUID,
    idempotency_key: str,
) -> Message | None:
    return await db.scalar(
        select(Message).where(
            Message.tenant_id == tenant_id,
            Message.idempotency_key == idempotency_key,
        )
    )


def message_identity_matches(
    message: Message, payload: MessageCreate, conversation_id: UUID
) -> bool:
    return (
        message.conversation_id == conversation_id
        and message.external_identity_id == payload.external_identity_id
        and message.direction == payload.direction.value
        and message.author_type == payload.author_type.value
        and message.message_type == payload.message_type.value
        and message.external_message_id == payload.external_message_id
    )


def raise_idempotency_conflict() -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Idempotency key is already bound to a different message",
    )


async def load_attachments(
    db: AsyncSession,
    tenant_id: UUID,
    message_ids: list[UUID],
) -> dict[UUID, list[MessageAttachment]]:
    grouped: dict[UUID, list[MessageAttachment]] = {message_id: [] for message_id in message_ids}
    if not message_ids:
        return grouped
    attachments = (
        await db.scalars(
            select(MessageAttachment)
            .where(
                MessageAttachment.tenant_id == tenant_id,
                MessageAttachment.message_id.in_(message_ids),
            )
            .order_by(
                MessageAttachment.message_id, MessageAttachment.created_at, MessageAttachment.id
            )
        )
    ).all()
    for attachment in attachments:
        grouped[attachment.message_id].append(attachment)
    return grouped


async def build_message_response(db: AsyncSession, message: Message) -> MessageResponse:
    attachments = await load_attachments(db, message.tenant_id, [message.id])
    return message_response(message, attachments[message.id])


@router.post("", response_model=ConversationResponse)
async def create_conversation(
    payload: ConversationCreate,
    response: Response,
    context: TenantContextDependency,
    db: DbSession,
) -> ConversationResponse:
    require_tenant_role(context, WriteRole)
    contact = await load_contact(db, context.tenant.id, payload.contact_id)
    conversation = Conversation(
        tenant_id=context.tenant.id,
        contact_id=contact.id,
        status=ConversationStatus.OPEN.value,
        subject=payload.subject,
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    response.status_code = status.HTTP_201_CREATED
    return conversation_response(conversation)


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    context: TenantContextDependency,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ConversationResponse]:
    conversations = (
        await db.scalars(
            select(Conversation)
            .where(Conversation.tenant_id == context.tenant.id)
            .order_by(Conversation.updated_at.desc(), Conversation.id)
            .limit(limit)
        )
    ).all()
    return [conversation_response(conversation) for conversation in conversations]


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> ConversationResponse:
    conversation = await load_conversation(db, context.tenant.id, conversation_id)
    return conversation_response(conversation)


@router.post("/{conversation_id}/messages", response_model=MessageResponse)
async def create_message(
    conversation_id: UUID,
    payload: MessageCreate,
    response: Response,
    context: TenantContextDependency,
    db: DbSession,
) -> MessageResponse:
    require_tenant_role(context, WriteRole)
    conversation = await load_conversation(db, context.tenant.id, conversation_id)
    resolved_conversation_id = conversation.id
    await validate_external_identity(
        db,
        context.tenant.id,
        conversation.contact_id,
        payload.external_identity_id,
    )

    if payload.idempotency_key is not None:
        existing = await load_message_by_idempotency_key(
            db,
            context.tenant.id,
            payload.idempotency_key,
        )
        if existing is not None:
            if not message_identity_matches(existing, payload, resolved_conversation_id):
                raise_idempotency_conflict()
            return await build_message_response(db, existing)

    occurred_at = payload.occurred_at or datetime.now(UTC)
    message = Message(
        tenant_id=context.tenant.id,
        conversation_id=resolved_conversation_id,
        external_identity_id=payload.external_identity_id,
        direction=payload.direction.value,
        author_type=payload.author_type.value,
        message_type=payload.message_type.value,
        text=payload.text,
        external_message_id=payload.external_message_id,
        idempotency_key=payload.idempotency_key,
        external_metadata=payload.metadata,
        occurred_at=occurred_at,
    )

    try:
        db.add(message)
        await db.flush()
        for attachment_payload in payload.attachments:
            db.add(
                MessageAttachment(
                    tenant_id=context.tenant.id,
                    message_id=message.id,
                    media_type=attachment_payload.media_type,
                    mime_type=attachment_payload.mime_type,
                    filename=attachment_payload.filename,
                    size_bytes=attachment_payload.size_bytes,
                    external_media_id=attachment_payload.external_media_id,
                    storage_key=attachment_payload.storage_key,
                    media_metadata=attachment_payload.metadata,
                )
            )
        if conversation.last_message_at is None or occurred_at > conversation.last_message_at:
            conversation.last_message_at = occurred_at
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if payload.idempotency_key is None:
            raise
        raced_message = await load_message_by_idempotency_key(
            db,
            context.tenant.id,
            payload.idempotency_key,
        )
        if raced_message is None:
            raise
        if not message_identity_matches(raced_message, payload, resolved_conversation_id):
            raise_idempotency_conflict()
        return await build_message_response(db, raced_message)

    await db.refresh(message)
    response.status_code = status.HTTP_201_CREATED
    return await build_message_response(db, message)


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    conversation_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[MessageResponse]:
    conversation = await load_conversation(db, context.tenant.id, conversation_id)
    messages = list(
        (
            await db.scalars(
                select(Message)
                .where(
                    Message.tenant_id == context.tenant.id,
                    Message.conversation_id == conversation.id,
                )
                .order_by(Message.occurred_at, Message.created_at, Message.id)
                .limit(limit)
            )
        ).all()
    )
    attachments = await load_attachments(
        db,
        context.tenant.id,
        [message.id for message in messages],
    )
    return [message_response(message, attachments[message.id]) for message in messages]
