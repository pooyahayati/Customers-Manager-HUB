import secrets
from json import JSONDecodeError
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.channel_gateway import ChannelProviderError
from customers_manager_hub.channel_models import ChannelCredentialKind, ChannelType
from customers_manager_hub.channel_queue import ChannelJobQueue
from customers_manager_hub.channel_runtime import (
    load_channel_account,
    load_channel_secret,
    mark_event_enqueued,
    persist_inbound_event,
)
from customers_manager_hub.channel_security import (
    ChannelSecretConfigurationError,
    ChannelSecretDecryptionError,
)
from customers_manager_hub.config import Settings
from customers_manager_hub.database import get_db_session
from customers_manager_hub.telegram import normalize_telegram_update

router = APIRouter(prefix="/api/v1/webhooks", tags=["channel-webhooks"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
_MAX_TELEGRAM_WEBHOOK_BYTES = 1_000_000


def get_channel_queue(request: Request) -> ChannelJobQueue:
    return cast(ChannelJobQueue, request.app.state.channel_queue)


def get_runtime_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


@router.post("/telegram/{channel_account_id}")
async def telegram_webhook(
    channel_account_id: UUID,
    request: Request,
    db: DbSession,
    queue: Annotated[ChannelJobQueue, Depends(get_channel_queue)],
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> dict[str, bool]:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_TELEGRAM_WEBHOOK_BYTES:
                raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from exc

    account = await load_channel_account(db, channel_account_id)
    if (
        account is None
        or not account.is_active
        or account.channel_type != ChannelType.TELEGRAM.value
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    supplied_secret = request.headers.get("x-telegram-bot-api-secret-token")
    if supplied_secret is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        expected_secret = await load_channel_secret(
            db,
            settings,
            account,
            ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET,
        )
    except (ChannelSecretConfigurationError, ChannelSecretDecryptionError) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc
    if not secrets.compare_digest(supplied_secret, expected_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    try:
        payload = await request.json()
    except (JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from exc
    try:
        normalized = normalize_telegram_update(payload, account.id)
    except ChannelProviderError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from exc

    persisted = await persist_inbound_event(
        db,
        account,
        external_event_id=normalized.external_event_id,
        canonical=normalized.message,
        ignored_reason=normalized.ignored_reason,
    )
    if persisted.should_enqueue:
        try:
            await queue.enqueue(persisted.event_id)
            await mark_event_enqueued(db, persisted.event_id)
        except RedisError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc

    return {"ok": True}
