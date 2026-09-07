import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from redis.exceptions import RedisError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.channel_gateway import CanonicalInboundMessage
from customers_manager_hub.channel_models import (
    ChannelAccount,
    ChannelAccountCapability,
    ChannelCapability,
    ChannelType,
    ConversationChannelBinding,
    WebsiteChannelOrigin,
    WebsiteChatSession,
)
from customers_manager_hub.channel_queue import ChannelJobQueue
from customers_manager_hub.channel_runtime import (
    load_channel_account,
    mark_event_enqueued,
    persist_inbound_event,
)
from customers_manager_hub.database import get_db_session
from customers_manager_hub.models import (
    AuditEvent,
    Message,
    MessageAuthorType,
    MessageDirection,
    MessageType,
    TenantRole,
)
from customers_manager_hub.tenants import TenantContextDependency, require_tenant_role
from customers_manager_hub.website import normalize_website_origin

admin_router = APIRouter(
    prefix="/api/v1/tenants/{tenant_id}/channels",
    tags=["website-chat-admin"],
)
public_router = APIRouter(prefix="/api/v1/public/website", tags=["website-chat-public"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
ConfigWriteRole = frozenset({TenantRole.OWNER, TenantRole.ADMIN})
_SESSION_LIFETIME = timedelta(days=30)
_SESSION_TOKEN_HEADER = "x-website-session-token"
_MAX_PUBLIC_MESSAGES = 100


def get_channel_queue(request: Request) -> ChannelJobQueue:
    return cast(ChannelJobQueue, request.app.state.channel_queue)


class WebsiteChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    allowed_origins: list[str] = Field(min_length=1, max_length=20)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Channel name must not be blank")
        return normalized

    @field_validator("allowed_origins")
    @classmethod
    def normalize_origins(cls, value: list[str]) -> list[str]:
        normalized = [normalize_website_origin(item) for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Website origins must be unique")
        return normalized


class WebsiteOriginsUpdate(BaseModel):
    allowed_origins: list[str] = Field(min_length=1, max_length=20)

    @field_validator("allowed_origins")
    @classmethod
    def normalize_origins(cls, value: list[str]) -> list[str]:
        normalized = [normalize_website_origin(item) for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Website origins must be unique")
        return normalized


class WebsiteChannelResponse(BaseModel):
    id: UUID
    channel_type: ChannelType
    name: str
    is_active: bool
    supported_capabilities: list[ChannelCapability]
    allowed_origins: list[str]


class WebsiteSessionResponse(BaseModel):
    session_id: UUID
    session_token: str
    expires_at: datetime


class WebsiteMessageCreate(BaseModel):
    client_message_id: UUID
    text: str = Field(min_length=1, max_length=4096)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Message text must not be blank")
        if "\x00" in normalized:
            raise ValueError("Message text contains an invalid character")
        return normalized


class WebsiteMessageAccepted(BaseModel):
    accepted: bool
    event_id: UUID


class WebsitePublicMessage(BaseModel):
    id: UUID
    direction: MessageDirection
    author_type: MessageAuthorType
    text: str
    occurred_at: datetime


def _token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _cors_headers(origin: str, *, no_store: bool = False) -> dict[str, str]:
    headers = {
        "Access-Control-Allow-Origin": origin,
        "Vary": "Origin",
    }
    if no_store:
        headers["Cache-Control"] = "no-store"
    return headers


def _set_cors(response: Response, origin: str, *, no_store: bool = False) -> None:
    response.headers.update(_cors_headers(origin, no_store=no_store))


async def _load_website_account(
    db: AsyncSession,
    account_id: UUID,
    *,
    tenant_id: UUID | None = None,
    require_active: bool = False,
) -> ChannelAccount:
    account = await load_channel_account(db, account_id, tenant_id=tenant_id)
    if account is None or account.channel_type != ChannelType.WEBSITE.value:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Website channel not found"
        )
    if require_active and not account.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Website channel not found"
        )
    return account


async def _load_origins(
    db: AsyncSession,
    tenant_id: UUID,
    account_ids: list[UUID],
) -> dict[UUID, list[str]]:
    result: dict[UUID, list[str]] = {account_id: [] for account_id in account_ids}
    if not account_ids:
        return result
    rows = list(
        (
            await db.scalars(
                select(WebsiteChannelOrigin)
                .where(
                    WebsiteChannelOrigin.tenant_id == tenant_id,
                    WebsiteChannelOrigin.channel_account_id.in_(account_ids),
                )
                .order_by(WebsiteChannelOrigin.origin)
            )
        ).all()
    )
    for row in rows:
        result[row.channel_account_id].append(row.origin)
    return result


def _website_response(account: ChannelAccount, origins: list[str]) -> WebsiteChannelResponse:
    return WebsiteChannelResponse(
        id=account.id,
        channel_type=ChannelType.WEBSITE,
        name=account.name,
        is_active=account.is_active,
        supported_capabilities=[ChannelCapability.TEXT, ChannelCapability.OUTBOUND_TEXT],
        allowed_origins=origins,
    )


async def _require_allowed_origin(
    db: AsyncSession,
    account: ChannelAccount,
    request: Request,
) -> str:
    supplied = request.headers.get("origin")
    if supplied is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origin is required")
    try:
        normalized = normalize_website_origin(supplied)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Origin is not allowed"
        ) from exc
    allowed = await db.scalar(
        select(WebsiteChannelOrigin.id).where(
            WebsiteChannelOrigin.tenant_id == account.tenant_id,
            WebsiteChannelOrigin.channel_account_id == account.id,
            WebsiteChannelOrigin.origin == normalized,
        )
    )
    if allowed is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origin is not allowed")
    return normalized


async def _require_session(
    db: AsyncSession,
    account: ChannelAccount,
    session_id: UUID,
    request: Request,
    *,
    origin: str,
) -> WebsiteChatSession:
    presented = request.headers.get(_SESSION_TOKEN_HEADER)
    if presented is None or len(presented) > 512:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Website session",
            headers=_cors_headers(origin, no_store=True),
        )
    chat_session = await db.scalar(
        select(WebsiteChatSession).where(
            WebsiteChatSession.id == session_id,
            WebsiteChatSession.tenant_id == account.tenant_id,
            WebsiteChatSession.channel_account_id == account.id,
        )
    )
    if chat_session is None or chat_session.expires_at <= datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Website session",
            headers=_cors_headers(origin, no_store=True),
        )
    if not secrets.compare_digest(chat_session.token_hash, _token_hash(presented)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Website session",
            headers=_cors_headers(origin, no_store=True),
        )
    return chat_session


@admin_router.post("/website", response_model=WebsiteChannelResponse, status_code=201)
async def create_website_channel(
    payload: WebsiteChannelCreate,
    context: TenantContextDependency,
    db: DbSession,
) -> WebsiteChannelResponse:
    require_tenant_role(context, ConfigWriteRole)
    account = ChannelAccount(
        tenant_id=context.tenant.id,
        channel_type=ChannelType.WEBSITE.value,
        name=payload.name,
        external_account_id=f"website-{uuid4().hex}",
        external_username=None,
        is_active=True,
    )
    db.add(account)
    try:
        await db.flush()
        db.add(
            ChannelAccountCapability(
                tenant_id=context.tenant.id,
                channel_account_id=account.id,
                capability=ChannelCapability.TEXT.value,
                enabled=True,
            )
        )
        for origin in payload.allowed_origins:
            db.add(
                WebsiteChannelOrigin(
                    tenant_id=context.tenant.id,
                    channel_account_id=account.id,
                    origin=origin,
                )
            )
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="channel.website.created",
                target_type="channel_account",
                target_id=account.id,
                details={"origin_count": len(payload.allowed_origins)},
            )
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Website channel configuration conflicts with an existing resource",
        ) from exc
    await db.refresh(account)
    return _website_response(account, sorted(payload.allowed_origins))


@admin_router.get("/website", response_model=list[WebsiteChannelResponse])
async def list_website_channels(
    context: TenantContextDependency,
    db: DbSession,
) -> list[WebsiteChannelResponse]:
    accounts = list(
        (
            await db.scalars(
                select(ChannelAccount)
                .where(
                    ChannelAccount.tenant_id == context.tenant.id,
                    ChannelAccount.channel_type == ChannelType.WEBSITE.value,
                )
                .order_by(ChannelAccount.created_at, ChannelAccount.id)
            )
        ).all()
    )
    origins = await _load_origins(db, context.tenant.id, [account.id for account in accounts])
    return [_website_response(account, origins[account.id]) for account in accounts]


@admin_router.get("/website/{channel_account_id}", response_model=WebsiteChannelResponse)
async def get_website_channel(
    channel_account_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> WebsiteChannelResponse:
    account = await _load_website_account(
        db,
        channel_account_id,
        tenant_id=context.tenant.id,
    )
    origins = await _load_origins(db, context.tenant.id, [account.id])
    return _website_response(account, origins[account.id])


@admin_router.put(
    "/website/{channel_account_id}/origins",
    response_model=WebsiteChannelResponse,
)
async def update_website_origins(
    channel_account_id: UUID,
    payload: WebsiteOriginsUpdate,
    context: TenantContextDependency,
    db: DbSession,
) -> WebsiteChannelResponse:
    require_tenant_role(context, ConfigWriteRole)
    account = await _load_website_account(
        db,
        channel_account_id,
        tenant_id=context.tenant.id,
    )
    await db.execute(
        delete(WebsiteChannelOrigin).where(
            WebsiteChannelOrigin.tenant_id == context.tenant.id,
            WebsiteChannelOrigin.channel_account_id == account.id,
        )
    )
    for origin in payload.allowed_origins:
        db.add(
            WebsiteChannelOrigin(
                tenant_id=context.tenant.id,
                channel_account_id=account.id,
                origin=origin,
            )
        )
    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="channel.website.origins.updated",
            target_type="channel_account",
            target_id=account.id,
            details={"origin_count": len(payload.allowed_origins)},
        )
    )
    await db.commit()
    await db.refresh(account)
    return _website_response(account, sorted(payload.allowed_origins))


@public_router.options("/{channel_account_id}/{path:path}", status_code=204)
async def website_preflight(
    channel_account_id: UUID,
    path: str,
    request: Request,
    db: DbSession,
) -> Response:
    del path
    account = await _load_website_account(db, channel_account_id, require_active=True)
    origin = await _require_allowed_origin(db, account, request)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _set_cors(response, origin)
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Website-Session-Token"
    response.headers["Access-Control-Max-Age"] = "600"
    return response


@public_router.post(
    "/{channel_account_id}/sessions",
    response_model=WebsiteSessionResponse,
    status_code=201,
)
async def create_website_session(
    channel_account_id: UUID,
    request: Request,
    response: Response,
    db: DbSession,
) -> WebsiteSessionResponse:
    account = await _load_website_account(db, channel_account_id, require_active=True)
    origin = await _require_allowed_origin(db, account, request)
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + _SESSION_LIFETIME
    chat_session = WebsiteChatSession(
        tenant_id=account.tenant_id,
        channel_account_id=account.id,
        visitor_id=uuid4(),
        token_hash=_token_hash(raw_token),
        expires_at=expires_at,
    )
    db.add(chat_session)
    await db.commit()
    await db.refresh(chat_session)
    _set_cors(response, origin, no_store=True)
    return WebsiteSessionResponse(
        session_id=chat_session.id,
        session_token=raw_token,
        expires_at=chat_session.expires_at,
    )


@public_router.post(
    "/{channel_account_id}/sessions/{session_id}/messages",
    response_model=WebsiteMessageAccepted,
    status_code=202,
)
async def post_website_message(
    channel_account_id: UUID,
    session_id: UUID,
    payload: WebsiteMessageCreate,
    request: Request,
    response: Response,
    db: DbSession,
    queue: Annotated[ChannelJobQueue, Depends(get_channel_queue)],
) -> WebsiteMessageAccepted:
    account = await _load_website_account(db, channel_account_id, require_active=True)
    origin = await _require_allowed_origin(db, account, request)
    _set_cors(response, origin, no_store=True)
    chat_session = await _require_session(db, account, session_id, request, origin=origin)
    now = datetime.now(UTC)
    canonical = CanonicalInboundMessage(
        channel_type=ChannelType.WEBSITE,
        channel_account_id=account.id,
        external_event_id=f"website:{chat_session.id}:{payload.client_message_id}",
        external_message_id=str(payload.client_message_id),
        external_thread_id=str(chat_session.id),
        sender_namespace="website:visitor",
        sender_external_id=str(chat_session.visitor_id),
        sender_display_name=None,
        message_type=MessageType.TEXT,
        text=payload.text,
        occurred_at=now,
        metadata={"website_session_id": str(chat_session.id)},
    )
    persisted = await persist_inbound_event(
        db,
        account,
        external_event_id=canonical.external_event_id,
        canonical=canonical,
        ignored_reason=None,
    )
    if persisted.should_enqueue:
        try:
            await queue.enqueue(persisted.event_id)
            await mark_event_enqueued(db, persisted.event_id)
        except RedisError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                headers=_cors_headers(origin, no_store=True),
            ) from exc
    return WebsiteMessageAccepted(accepted=True, event_id=persisted.event_id)


@public_router.get(
    "/{channel_account_id}/sessions/{session_id}/messages",
    response_model=list[WebsitePublicMessage],
)
async def list_website_messages(
    channel_account_id: UUID,
    session_id: UUID,
    request: Request,
    response: Response,
    db: DbSession,
) -> list[WebsitePublicMessage]:
    account = await _load_website_account(db, channel_account_id, require_active=True)
    origin = await _require_allowed_origin(db, account, request)
    _set_cors(response, origin, no_store=True)
    await _require_session(db, account, session_id, request, origin=origin)
    binding = await db.scalar(
        select(ConversationChannelBinding).where(
            ConversationChannelBinding.tenant_id == account.tenant_id,
            ConversationChannelBinding.channel_account_id == account.id,
            ConversationChannelBinding.external_thread_id == str(session_id),
        )
    )
    if binding is None:
        return []
    rows = list(
        (
            await db.scalars(
                select(Message)
                .where(
                    Message.tenant_id == account.tenant_id,
                    Message.conversation_id == binding.conversation_id,
                    Message.message_type == MessageType.TEXT.value,
                    Message.text.is_not(None),
                )
                .order_by(
                    Message.occurred_at.desc(),
                    Message.created_at.desc(),
                    Message.id.desc(),
                )
                .limit(_MAX_PUBLIC_MESSAGES)
            )
        ).all()
    )
    rows.reverse()
    return [
        WebsitePublicMessage(
            id=item.id,
            direction=MessageDirection(item.direction),
            author_type=MessageAuthorType(item.author_type),
            text=cast(str, item.text),
            occurred_at=item.occurred_at,
        )
        for item in rows
    ]
