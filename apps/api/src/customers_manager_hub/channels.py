import secrets
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.channel_gateway import ChannelProviderError, ChannelRegistry
from customers_manager_hub.channel_models import (
    ChannelAccount,
    ChannelAccountCapability,
    ChannelCapability,
    ChannelCredential,
    ChannelCredentialKind,
    ChannelType,
)
from customers_manager_hub.channel_runtime import (
    ChannelRuntimeError,
    dispatch_text,
    load_channel_account,
    load_channel_secret,
)
from customers_manager_hub.channel_security import (
    ChannelSecretConfigurationError,
    encrypt_channel_secret,
)
from customers_manager_hub.config import Settings
from customers_manager_hub.conversations import MessageResponse, build_message_response
from customers_manager_hub.database import get_db_session
from customers_manager_hub.models import AuditEvent, MessageAuthorType, TenantRole
from customers_manager_hub.tenants import TenantContextDependency, require_tenant_role

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/channels", tags=["channels"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
ConfigWriteRole = frozenset({TenantRole.OWNER, TenantRole.ADMIN})
DispatchRole = frozenset(
    {TenantRole.OWNER, TenantRole.ADMIN, TenantRole.SUPERVISOR, TenantRole.AGENT}
)
_INBOUND_CAPABILITIES = frozenset({ChannelCapability.TEXT, ChannelCapability.VOICE})


def default_inbound_capabilities() -> list[ChannelCapability]:
    return [ChannelCapability.TEXT, ChannelCapability.VOICE]


def get_channel_registry(request: Request) -> ChannelRegistry:
    return cast(ChannelRegistry, request.app.state.channel_registry)


def get_runtime_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


class TelegramChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    bot_token: SecretStr
    enabled_inbound_types: list[ChannelCapability] = Field(
        default_factory=default_inbound_capabilities,
        max_length=2,
    )

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Channel name must not be blank")
        return normalized

    @field_validator("enabled_inbound_types")
    @classmethod
    def validate_inbound_types(cls, value: list[ChannelCapability]) -> list[ChannelCapability]:
        if any(capability not in _INBOUND_CAPABILITIES for capability in value):
            raise ValueError("Only text and voice can be configured as inbound capabilities")
        if len(value) != len(set(value)):
            raise ValueError("Inbound capabilities must be unique")
        return value


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None
    enabled_inbound_types: list[ChannelCapability] | None = Field(default=None, max_length=2)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Channel name must not be blank")
        return normalized

    @field_validator("enabled_inbound_types")
    @classmethod
    def validate_inbound_types(
        cls, value: list[ChannelCapability] | None
    ) -> list[ChannelCapability] | None:
        if value is None:
            return None
        if any(capability not in _INBOUND_CAPABILITIES for capability in value):
            raise ValueError("Only text and voice can be configured as inbound capabilities")
        if len(value) != len(set(value)):
            raise ValueError("Inbound capabilities must be unique")
        return value

    @model_validator(mode="after")
    def require_change(self) -> ChannelUpdate:
        if self.name is None and self.is_active is None and self.enabled_inbound_types is None:
            raise ValueError("At least one channel setting must be supplied")
        return self


class ChannelAccountResponse(BaseModel):
    id: UUID
    channel_type: ChannelType
    name: str
    external_account_id: str
    external_username: str | None
    is_active: bool
    supported_capabilities: list[ChannelCapability]
    enabled_inbound_types: list[ChannelCapability]
    credentials_configured: bool


class WebhookRegistrationResponse(BaseModel):
    registered: bool
    url: str


class ChannelTextDispatch(BaseModel):
    conversation_id: UUID
    text: str = Field(min_length=1, max_length=4096)
    idempotency_key: str = Field(min_length=1, max_length=255)

    @field_validator("text", "idempotency_key")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value must not be blank")
        return normalized


async def _load_capabilities(
    db: AsyncSession,
    tenant_id: UUID,
    account_ids: list[UUID],
) -> dict[UUID, set[ChannelCapability]]:
    result: dict[UUID, set[ChannelCapability]] = {account_id: set() for account_id in account_ids}
    if not account_ids:
        return result
    rows = (
        await db.scalars(
            select(ChannelAccountCapability).where(
                ChannelAccountCapability.tenant_id == tenant_id,
                ChannelAccountCapability.channel_account_id.in_(account_ids),
                ChannelAccountCapability.enabled.is_(True),
            )
        )
    ).all()
    for row in rows:
        result[row.channel_account_id].add(ChannelCapability(row.capability))
    return result


async def _load_configured_credentials(
    db: AsyncSession,
    tenant_id: UUID,
    account_ids: list[UUID],
) -> dict[UUID, set[ChannelCredentialKind]]:
    result: dict[UUID, set[ChannelCredentialKind]] = {
        account_id: set() for account_id in account_ids
    }
    if not account_ids:
        return result
    rows = (
        await db.scalars(
            select(ChannelCredential).where(
                ChannelCredential.tenant_id == tenant_id,
                ChannelCredential.channel_account_id.in_(account_ids),
            )
        )
    ).all()
    for row in rows:
        result[row.channel_account_id].add(ChannelCredentialKind(row.kind))
    return result


def _channel_response(
    account: ChannelAccount,
    registry: ChannelRegistry,
    enabled: set[ChannelCapability],
    credential_kinds: set[ChannelCredentialKind],
) -> ChannelAccountResponse:
    channel_type = ChannelType(account.channel_type)
    adapter = registry.get(channel_type)
    required_credentials = {
        ChannelCredentialKind.TELEGRAM_BOT_TOKEN,
        ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET,
    }
    return ChannelAccountResponse(
        id=account.id,
        channel_type=channel_type,
        name=account.name,
        external_account_id=account.external_account_id,
        external_username=account.external_username,
        is_active=account.is_active,
        supported_capabilities=sorted(adapter.capabilities, key=lambda item: item.value),
        enabled_inbound_types=sorted(enabled, key=lambda item: item.value),
        credentials_configured=required_credentials.issubset(credential_kinds),
    )


def _raise_provider_error(exc: ChannelProviderError) -> None:
    http_status = (
        status.HTTP_503_SERVICE_UNAVAILABLE if exc.retryable else status.HTTP_400_BAD_REQUEST
    )
    raise HTTPException(status_code=http_status, detail="Telegram request failed") from exc


def _raise_runtime_error(exc: ChannelRuntimeError) -> None:
    if "idempotency_conflict" in exc.code:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Idempotency conflict"
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Channel resource not found"
    ) from exc


@router.post("/telegram", response_model=ChannelAccountResponse)
async def create_telegram_channel(
    payload: TelegramChannelCreate,
    response: Response,
    context: TenantContextDependency,
    db: DbSession,
    registry: Annotated[ChannelRegistry, Depends(get_channel_registry)],
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> ChannelAccountResponse:
    require_tenant_role(context, ConfigWriteRole)
    adapter = registry.get(ChannelType.TELEGRAM)
    bot_token = payload.bot_token.get_secret_value()
    try:
        identity = await adapter.validate_account(bot_token)
    except ChannelProviderError as exc:
        _raise_provider_error(exc)

    account = ChannelAccount(
        tenant_id=context.tenant.id,
        channel_type=ChannelType.TELEGRAM.value,
        name=payload.name,
        external_account_id=identity.external_account_id,
        external_username=identity.username,
        is_active=True,
    )
    db.add(account)
    try:
        await db.flush()
        webhook_secret = secrets.token_urlsafe(32)
        encrypted_token = encrypt_channel_secret(
            settings,
            context.tenant.id,
            account.id,
            ChannelCredentialKind.TELEGRAM_BOT_TOKEN,
            bot_token,
        )
        encrypted_webhook_secret = encrypt_channel_secret(
            settings,
            context.tenant.id,
            account.id,
            ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET,
            webhook_secret,
        )
        for kind, encrypted in (
            (ChannelCredentialKind.TELEGRAM_BOT_TOKEN, encrypted_token),
            (ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET, encrypted_webhook_secret),
        ):
            db.add(
                ChannelCredential(
                    tenant_id=context.tenant.id,
                    channel_account_id=account.id,
                    kind=kind.value,
                    ciphertext=encrypted.ciphertext,
                    nonce=encrypted.nonce,
                    key_version=encrypted.key_version,
                )
            )
        enabled = set(payload.enabled_inbound_types)
        for capability in (ChannelCapability.TEXT, ChannelCapability.VOICE):
            db.add(
                ChannelAccountCapability(
                    tenant_id=context.tenant.id,
                    channel_account_id=account.id,
                    capability=capability.value,
                    enabled=capability in enabled,
                )
            )
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="channel.telegram.created",
                target_type="channel_account",
                target_id=account.id,
                details={
                    "channel_type": ChannelType.TELEGRAM.value,
                    "enabled_inbound_types": sorted(item.value for item in enabled),
                },
            )
        )
        await db.commit()
    except ChannelSecretConfigurationError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Channel credential encryption is not configured",
        ) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Telegram bot is already registered",
        ) from exc

    await db.refresh(account)
    response.status_code = status.HTTP_201_CREATED
    configured = {
        ChannelCredentialKind.TELEGRAM_BOT_TOKEN,
        ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET,
    }
    return _channel_response(account, registry, enabled, configured)


@router.get("", response_model=list[ChannelAccountResponse])
async def list_channels(
    context: TenantContextDependency,
    db: DbSession,
    registry: Annotated[ChannelRegistry, Depends(get_channel_registry)],
) -> list[ChannelAccountResponse]:
    accounts = list(
        (
            await db.scalars(
                select(ChannelAccount)
                .where(ChannelAccount.tenant_id == context.tenant.id)
                .order_by(ChannelAccount.created_at, ChannelAccount.id)
            )
        ).all()
    )
    ids = [account.id for account in accounts]
    capabilities = await _load_capabilities(db, context.tenant.id, ids)
    credentials = await _load_configured_credentials(db, context.tenant.id, ids)
    return [
        _channel_response(account, registry, capabilities[account.id], credentials[account.id])
        for account in accounts
    ]


@router.get("/{channel_account_id}", response_model=ChannelAccountResponse)
async def get_channel(
    channel_account_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
    registry: Annotated[ChannelRegistry, Depends(get_channel_registry)],
) -> ChannelAccountResponse:
    account = await load_channel_account(db, channel_account_id, tenant_id=context.tenant.id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    capabilities = await _load_capabilities(db, context.tenant.id, [account.id])
    credentials = await _load_configured_credentials(db, context.tenant.id, [account.id])
    return _channel_response(account, registry, capabilities[account.id], credentials[account.id])


@router.patch("/{channel_account_id}", response_model=ChannelAccountResponse)
async def update_channel(
    channel_account_id: UUID,
    payload: ChannelUpdate,
    context: TenantContextDependency,
    db: DbSession,
    registry: Annotated[ChannelRegistry, Depends(get_channel_registry)],
) -> ChannelAccountResponse:
    require_tenant_role(context, ConfigWriteRole)
    account = await load_channel_account(db, channel_account_id, tenant_id=context.tenant.id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    changed_fields: list[str] = []
    if payload.name is not None and payload.name != account.name:
        account.name = payload.name
        changed_fields.append("name")
    if payload.is_active is not None and payload.is_active != account.is_active:
        account.is_active = payload.is_active
        changed_fields.append("is_active")
    if payload.enabled_inbound_types is not None:
        desired = set(payload.enabled_inbound_types)
        rows = list(
            (
                await db.scalars(
                    select(ChannelAccountCapability).where(
                        ChannelAccountCapability.tenant_id == context.tenant.id,
                        ChannelAccountCapability.channel_account_id == account.id,
                    )
                )
            ).all()
        )
        by_capability = {ChannelCapability(row.capability): row for row in rows}
        for capability in (ChannelCapability.TEXT, ChannelCapability.VOICE):
            row = by_capability.get(capability)
            if row is None:
                row = ChannelAccountCapability(
                    tenant_id=context.tenant.id,
                    channel_account_id=account.id,
                    capability=capability.value,
                    enabled=capability in desired,
                )
                db.add(row)
            else:
                row.enabled = capability in desired
        changed_fields.append("enabled_inbound_types")

    if changed_fields:
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="channel.updated",
                target_type="channel_account",
                target_id=account.id,
                details={"changed_fields": changed_fields},
            )
        )
        await db.commit()
        await db.refresh(account)

    capabilities = await _load_capabilities(db, context.tenant.id, [account.id])
    credentials = await _load_configured_credentials(db, context.tenant.id, [account.id])
    return _channel_response(account, registry, capabilities[account.id], credentials[account.id])


@router.post("/{channel_account_id}/register-webhook", response_model=WebhookRegistrationResponse)
async def register_channel_webhook(
    channel_account_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
    registry: Annotated[ChannelRegistry, Depends(get_channel_registry)],
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> WebhookRegistrationResponse:
    require_tenant_role(context, ConfigWriteRole)
    account = await load_channel_account(db, channel_account_id, tenant_id=context.tenant.id)
    if account is None or account.channel_type != ChannelType.TELEGRAM.value:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    if settings.telegram_webhook_base_url is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram webhook base URL is not configured",
        )
    try:
        bot_token = await load_channel_secret(
            db, settings, account, ChannelCredentialKind.TELEGRAM_BOT_TOKEN
        )
        webhook_secret = await load_channel_secret(
            db, settings, account, ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET
        )
        webhook_url = f"{settings.telegram_webhook_base_url}/api/v1/webhooks/telegram/{account.id}"
        await registry.get(ChannelType.TELEGRAM).register_webhook(
            bot_token,
            webhook_url=webhook_url,
            webhook_secret=webhook_secret,
        )
    except ChannelProviderError as exc:
        _raise_provider_error(exc)
    except ChannelRuntimeError as exc:
        _raise_runtime_error(exc)

    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="channel.telegram.webhook_registered",
            target_type="channel_account",
            target_id=account.id,
            details={"webhook_url": webhook_url},
        )
    )
    await db.commit()
    return WebhookRegistrationResponse(registered=True, url=webhook_url)


@router.post("/{channel_account_id}/send-text", response_model=MessageResponse)
async def send_channel_text(
    channel_account_id: UUID,
    payload: ChannelTextDispatch,
    context: TenantContextDependency,
    db: DbSession,
    registry: Annotated[ChannelRegistry, Depends(get_channel_registry)],
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> MessageResponse:
    require_tenant_role(context, DispatchRole)
    try:
        message = await dispatch_text(
            db,
            registry,
            settings,
            tenant_id=context.tenant.id,
            channel_account_id=channel_account_id,
            conversation_id=payload.conversation_id,
            text=payload.text,
            idempotency_key=payload.idempotency_key,
            author_type=MessageAuthorType.HUMAN,
        )
    except ChannelProviderError as exc:
        _raise_provider_error(exc)
    except ChannelRuntimeError as exc:
        _raise_runtime_error(exc)
    return await build_message_response(db, message)
