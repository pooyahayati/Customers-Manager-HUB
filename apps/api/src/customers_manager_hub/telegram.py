from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote
from uuid import UUID

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from customers_manager_hub.channel_gateway import (
    CanonicalAttachment,
    CanonicalInboundMessage,
    ChannelAccountIdentity,
    ChannelMediaReference,
    ChannelProviderError,
    ChannelSendResult,
)
from customers_manager_hub.channel_models import ChannelCapability, ChannelType
from customers_manager_hub.models import MessageType

TELEGRAM_API_BASE_URL = "https://api.telegram.org"
_TELEGRAM_REQUEST_TIMEOUT_SECONDS = 10.0


class _TelegramUser(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    is_bot: bool
    first_name: str
    last_name: str | None = None
    username: str | None = None


class _TelegramChat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    type: str


class _TelegramVoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file_id: str
    file_unique_id: str
    duration: int
    mime_type: str | None = None
    file_size: int | None = None


class _TelegramMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    message_id: int
    date: int
    chat: _TelegramChat
    sender: _TelegramUser | None = Field(default=None, alias="from")
    text: str | None = None
    voice: _TelegramVoice | None = None


class _TelegramUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    update_id: int
    message: _TelegramMessage | None = None


class _TelegramFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file_id: str
    file_unique_id: str
    file_size: int | None = None
    file_path: str | None = None


class _TelegramSentMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message_id: int
    date: int


class _TelegramGetMeEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool
    result: _TelegramUser | None = None
    description: str | None = None
    error_code: int | None = None


class _TelegramBoolEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool
    result: bool | None = None
    description: str | None = None
    error_code: int | None = None


class _TelegramFileEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool
    result: _TelegramFile | None = None
    description: str | None = None
    error_code: int | None = None


class _TelegramMessageEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool
    result: _TelegramSentMessage | None = None
    description: str | None = None
    error_code: int | None = None


@dataclass(frozen=True, slots=True)
class TelegramNormalizedUpdate:
    external_event_id: str
    message: CanonicalInboundMessage | None
    ignored_reason: str | None = None


def _is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or status_code >= 500


def _bot_url(access_secret: str, method: str) -> str:
    token = access_secret.strip()
    if not token or any(character.isspace() for character in token) or "/" in token:
        raise ChannelProviderError("telegram_invalid_bot_token", retryable=False)
    return f"{TELEGRAM_API_BASE_URL}/bot{quote(token, safe=':._-')}/{method}"


async def _telegram_post(
    client: httpx2.AsyncClient,
    access_secret: str,
    method: str,
    payload: dict[str, object] | None = None,
) -> object:
    try:
        response = await client.post(
            _bot_url(access_secret, method),
            json=payload or {},
            timeout=_TELEGRAM_REQUEST_TIMEOUT_SECONDS,
        )
    except httpx2.TimeoutException as exc:
        raise ChannelProviderError("telegram_timeout", retryable=True) from exc
    except httpx2.TransportError as exc:
        raise ChannelProviderError("telegram_transport_error", retryable=True) from exc
    if response.status_code >= 400:
        raise ChannelProviderError(
            f"telegram_http_{response.status_code}",
            retryable=_is_retryable_status(response.status_code),
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ChannelProviderError("telegram_invalid_response", retryable=True) from exc


def _raise_api_error(ok: bool, error_code: int | None) -> None:
    if ok:
        return
    code = error_code or 0
    raise ChannelProviderError(
        f"telegram_api_{code or 'error'}",
        retryable=_is_retryable_status(code),
    )


def _sender_display_name(sender: _TelegramUser) -> str:
    parts = [part.strip() for part in (sender.first_name, sender.last_name or "") if part.strip()]
    if parts:
        return " ".join(parts)
    if sender.username:
        return sender.username
    return str(sender.id)


def normalize_telegram_update(
    payload: object,
    channel_account_id: UUID,
) -> TelegramNormalizedUpdate:
    try:
        update = _TelegramUpdate.model_validate(payload)
    except ValidationError as exc:
        raise ChannelProviderError("telegram_invalid_update", retryable=False) from exc

    external_event_id = str(update.update_id)
    message = update.message
    if message is None:
        return TelegramNormalizedUpdate(
            external_event_id=external_event_id,
            message=None,
            ignored_reason="unsupported_update_type",
        )
    if message.chat.type != "private" or message.sender is None or message.sender.is_bot:
        return TelegramNormalizedUpdate(
            external_event_id=external_event_id,
            message=None,
            ignored_reason="unsupported_chat_or_sender",
        )

    try:
        occurred_at = datetime.fromtimestamp(message.date, tz=UTC)
    except (OSError, OverflowError, ValueError) as exc:
        raise ChannelProviderError("telegram_invalid_message_timestamp", retryable=False) from exc

    attachments: tuple[CanonicalAttachment, ...] = ()
    text: str | None = None
    if message.text is not None and message.text.strip():
        message_type = MessageType.TEXT
        text = message.text
    elif message.voice is not None:
        message_type = MessageType.VOICE
        voice = message.voice
        attachments = (
            CanonicalAttachment(
                media_type=MessageType.VOICE,
                mime_type=voice.mime_type,
                size_bytes=voice.file_size,
                external_media_id=voice.file_id,
                metadata={
                    "telegram_file_unique_id": voice.file_unique_id,
                    "duration_seconds": voice.duration,
                },
            ),
        )
    else:
        return TelegramNormalizedUpdate(
            external_event_id=external_event_id,
            message=None,
            ignored_reason="unsupported_message_type",
        )

    metadata: dict[str, object] = {
        "channel_type": ChannelType.TELEGRAM.value,
        "channel_account_id": str(channel_account_id),
        "telegram_update_id": update.update_id,
        "telegram_chat_id": str(message.chat.id),
    }
    if message.sender.username:
        metadata["telegram_sender_username"] = message.sender.username

    return TelegramNormalizedUpdate(
        external_event_id=external_event_id,
        message=CanonicalInboundMessage(
            channel_type=ChannelType.TELEGRAM,
            channel_account_id=channel_account_id,
            external_event_id=external_event_id,
            external_message_id=str(message.message_id),
            external_thread_id=str(message.chat.id),
            sender_namespace="telegram:user",
            sender_external_id=str(message.sender.id),
            sender_display_name=_sender_display_name(message.sender),
            message_type=message_type,
            text=text,
            occurred_at=occurred_at,
            attachments=attachments,
            metadata=metadata,
        ),
    )


class TelegramAdapter:
    @property
    def channel_type(self) -> ChannelType:
        return ChannelType.TELEGRAM

    @property
    def capabilities(self) -> frozenset[ChannelCapability]:
        return frozenset(
            {
                ChannelCapability.TEXT,
                ChannelCapability.VOICE,
                ChannelCapability.OUTBOUND_TEXT,
            }
        )

    def __init__(self, client: httpx2.AsyncClient) -> None:
        self._client = client

    async def validate_account(self, access_secret: str) -> ChannelAccountIdentity:
        payload = await _telegram_post(self._client, access_secret, "getMe")
        try:
            envelope = _TelegramGetMeEnvelope.model_validate(payload)
        except ValidationError as exc:
            raise ChannelProviderError("telegram_invalid_response", retryable=True) from exc
        _raise_api_error(envelope.ok, envelope.error_code)
        if envelope.result is None or not envelope.result.is_bot:
            raise ChannelProviderError("telegram_invalid_bot_account", retryable=False)
        user = envelope.result
        return ChannelAccountIdentity(
            external_account_id=str(user.id),
            username=user.username,
            display_name=_sender_display_name(user),
        )

    async def register_webhook(
        self,
        access_secret: str,
        *,
        webhook_url: str,
        webhook_secret: str,
    ) -> None:
        payload = await _telegram_post(
            self._client,
            access_secret,
            "setWebhook",
            {
                "url": webhook_url,
                "secret_token": webhook_secret,
                "allowed_updates": ["message"],
                "drop_pending_updates": False,
            },
        )
        try:
            envelope = _TelegramBoolEnvelope.model_validate(payload)
        except ValidationError as exc:
            raise ChannelProviderError("telegram_invalid_response", retryable=True) from exc
        _raise_api_error(envelope.ok, envelope.error_code)
        if envelope.result is not True:
            raise ChannelProviderError("telegram_webhook_registration_failed", retryable=True)

    async def resolve_media(
        self,
        access_secret: str,
        external_media_id: str,
    ) -> ChannelMediaReference:
        payload = await _telegram_post(
            self._client,
            access_secret,
            "getFile",
            {"file_id": external_media_id},
        )
        try:
            envelope = _TelegramFileEnvelope.model_validate(payload)
        except ValidationError as exc:
            raise ChannelProviderError("telegram_invalid_response", retryable=True) from exc
        _raise_api_error(envelope.ok, envelope.error_code)
        if envelope.result is None:
            raise ChannelProviderError("telegram_file_missing", retryable=True)
        file = envelope.result
        return ChannelMediaReference(
            external_media_id=file.file_id,
            external_unique_id=file.file_unique_id,
            file_path=file.file_path,
            size_bytes=file.file_size,
        )

    async def send_text(
        self,
        access_secret: str,
        *,
        external_thread_id: str,
        text: str,
    ) -> ChannelSendResult:
        normalized = text.strip()
        if not normalized:
            raise ChannelProviderError("telegram_empty_text", retryable=False)
        if len(normalized) > 4096:
            raise ChannelProviderError("telegram_text_too_long", retryable=False)
        payload = await _telegram_post(
            self._client,
            access_secret,
            "sendMessage",
            {"chat_id": external_thread_id, "text": normalized},
        )
        try:
            envelope = _TelegramMessageEnvelope.model_validate(payload)
        except ValidationError as exc:
            raise ChannelProviderError("telegram_invalid_response", retryable=True) from exc
        _raise_api_error(envelope.ok, envelope.error_code)
        if envelope.result is None:
            raise ChannelProviderError("telegram_message_missing", retryable=True)
        try:
            occurred_at = datetime.fromtimestamp(envelope.result.date, tz=UTC)
        except (OSError, OverflowError, ValueError) as exc:
            raise ChannelProviderError("telegram_invalid_message_timestamp", retryable=True) from exc
        return ChannelSendResult(
            external_message_id=str(envelope.result.message_id),
            occurred_at=occurred_at,
        )
