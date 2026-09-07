from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote
from uuid import UUID

import httpx2
from sqlalchemy import select

from customers_manager_hub.ai_gateway import AIGateway, AIRoutingError, TranscriptionRequest
from customers_manager_hub.channel_models import (
    ChannelAccount,
    ChannelCredentialKind,
    ChannelInboundEvent,
    ChannelType,
)
from customers_manager_hub.channel_runtime import ChannelRuntimeError, load_channel_secret
from customers_manager_hub.channel_security import (
    ChannelSecretConfigurationError,
    ChannelSecretDecryptionError,
)
from customers_manager_hub.config import Settings
from customers_manager_hub.database import AsyncSessionFactory
from customers_manager_hub.models import Message, MessageAttachment, MessageType

TELEGRAM_FILE_BASE_URL = "https://api.telegram.org/file"
MAX_VOICE_AUDIO_BYTES = 20 * 1024 * 1024
MAX_TRANSCRIPT_CHARACTERS = 32_000
_SUPPORTED_AUDIO_MIME_TYPES = frozenset(
    {
        "audio/ogg",
        "audio/opus",
        "audio/mpeg",
        "audio/mp3",
        "audio/wav",
        "audio/aac",
        "audio/flac",
        "audio/m4a",
        "audio/webm",
    }
)
_MIME_ALIASES = {
    "audio/x-wav": "audio/wav",
    "audio/x-m4a": "audio/m4a",
}


class VoiceTranscriptionError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class VoiceMedia:
    data: bytes
    filename: str
    mime_type: str


def _retryable_http_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or status_code >= 500


def _normalize_mime_type(value: str | None) -> str:
    normalized = (value or "audio/ogg").split(";", 1)[0].strip().casefold()
    normalized = _MIME_ALIASES.get(normalized, normalized)
    if normalized not in _SUPPORTED_AUDIO_MIME_TYPES:
        raise VoiceTranscriptionError("voice_media_type_unsupported", retryable=False)
    return normalized


def _validate_file_path(file_path: str) -> str:
    normalized = file_path.strip().replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or "://" in normalized
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise VoiceTranscriptionError("telegram_voice_file_path_invalid", retryable=False)
    return normalized


class TelegramVoiceMediaDownloader:
    def __init__(self, client: httpx2.AsyncClient) -> None:
        self._client = client

    async def download(
        self,
        access_secret: str,
        *,
        file_path: str,
        mime_type: str | None,
        size_bytes: int | None,
    ) -> VoiceMedia:
        if size_bytes is not None and size_bytes > MAX_VOICE_AUDIO_BYTES:
            raise VoiceTranscriptionError("voice_media_too_large", retryable=False)
        normalized_path = _validate_file_path(file_path)
        token = access_secret.strip()
        if not token or any(character.isspace() for character in token) or "/" in token:
            raise VoiceTranscriptionError("telegram_invalid_bot_token", retryable=False)
        url = (
            f"{TELEGRAM_FILE_BASE_URL}/bot{quote(token, safe=':._-')}/"
            f"{quote(normalized_path, safe='/._-')}"
        )
        try:
            response = await self._client.get(url, timeout=15.0)
        except httpx2.TimeoutException as exc:
            raise VoiceTranscriptionError(
                "telegram_voice_download_timeout", retryable=True
            ) from exc
        except httpx2.TransportError as exc:
            raise VoiceTranscriptionError(
                "telegram_voice_download_transport_error", retryable=True
            ) from exc
        if response.status_code >= 400:
            raise VoiceTranscriptionError(
                f"telegram_voice_download_http_{response.status_code}",
                retryable=_retryable_http_status(response.status_code),
            )
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = -1
            if declared_size > MAX_VOICE_AUDIO_BYTES:
                raise VoiceTranscriptionError("voice_media_too_large", retryable=False)
        data = response.content
        if not data:
            raise VoiceTranscriptionError("voice_media_empty", retryable=False)
        if len(data) > MAX_VOICE_AUDIO_BYTES:
            raise VoiceTranscriptionError("voice_media_too_large", retryable=False)
        resolved_mime_type = _normalize_mime_type(mime_type)
        filename = PurePosixPath(normalized_path).name or "voice.ogg"
        return VoiceMedia(data=data, filename=filename, mime_type=resolved_mime_type)


def _normalize_transcript(value: str) -> str:
    transcript = value.replace("\x00", "").strip()
    if not transcript:
        raise VoiceTranscriptionError("voice_transcript_empty", retryable=False)
    if len(transcript) > MAX_TRANSCRIPT_CHARACTERS:
        raise VoiceTranscriptionError("voice_transcript_too_long", retryable=False)
    return transcript


async def process_voice_transcription(
    session_factory: AsyncSessionFactory,
    ai_gateway: AIGateway,
    settings: Settings,
    downloader: TelegramVoiceMediaDownloader,
    event_id: UUID,
) -> bool:
    """Transcribe one processed inbound voice event and enrich its original message."""
    async with session_factory() as db:
        event = await db.scalar(
            select(ChannelInboundEvent).where(ChannelInboundEvent.id == event_id)
        )
        if event is None:
            raise VoiceTranscriptionError("voice_channel_event_missing", retryable=False)
        if event.message_id is None:
            return False
        message = await db.scalar(
            select(Message).where(
                Message.id == event.message_id,
                Message.tenant_id == event.tenant_id,
            )
        )
        if message is None:
            raise VoiceTranscriptionError("voice_message_missing", retryable=False)
        if message.message_type != MessageType.VOICE.value:
            return False
        if message.text is not None and message.text.strip():
            return True

        attachment = await db.scalar(
            select(MessageAttachment).where(
                MessageAttachment.tenant_id == event.tenant_id,
                MessageAttachment.message_id == message.id,
                MessageAttachment.media_type == MessageType.VOICE.value,
            )
        )
        if attachment is None:
            raise VoiceTranscriptionError("voice_attachment_missing", retryable=False)
        file_path = attachment.media_metadata.get("telegram_file_path")
        if not isinstance(file_path, str) or not file_path.strip():
            raise VoiceTranscriptionError("telegram_voice_file_path_missing", retryable=True)
        account = await db.scalar(
            select(ChannelAccount).where(
                ChannelAccount.id == event.channel_account_id,
                ChannelAccount.tenant_id == event.tenant_id,
            )
        )
        if account is None or account.channel_type != ChannelType.TELEGRAM.value:
            raise VoiceTranscriptionError("voice_channel_unsupported", retryable=False)
        try:
            access_secret = await load_channel_secret(
                db,
                settings,
                account,
                ChannelCredentialKind.TELEGRAM_BOT_TOKEN,
            )
        except ChannelRuntimeError as exc:
            raise VoiceTranscriptionError(exc.code, retryable=False) from exc
        except ChannelSecretConfigurationError as exc:
            raise VoiceTranscriptionError(
                "voice_credential_configuration_error", retryable=False
            ) from exc
        except ChannelSecretDecryptionError as exc:
            raise VoiceTranscriptionError(
                "voice_credential_decryption_error", retryable=False
            ) from exc
        tenant_id = event.tenant_id
        message_id = message.id
        attachment_id = attachment.id
        mime_type = attachment.mime_type
        size_bytes = attachment.size_bytes

    media = await downloader.download(
        access_secret,
        file_path=file_path,
        mime_type=mime_type,
        size_bytes=size_bytes,
    )
    try:
        result = await ai_gateway.transcribe(
            tenant_id,
            TranscriptionRequest(
                audio=media.data,
                filename=media.filename,
                mime_type=media.mime_type,
            ),
        )
    except AIRoutingError as exc:
        raise VoiceTranscriptionError(exc.code, retryable=exc.retryable) from exc
    transcript = _normalize_transcript(result.text)

    async with session_factory() as db:
        message = await db.scalar(
            select(Message)
            .where(
                Message.id == message_id,
                Message.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        if message is None:
            raise VoiceTranscriptionError("voice_message_missing", retryable=False)
        if message.text is not None and message.text.strip():
            return True
        attachment = await db.scalar(
            select(MessageAttachment).where(
                MessageAttachment.id == attachment_id,
                MessageAttachment.tenant_id == tenant_id,
                MessageAttachment.message_id == message_id,
            )
        )
        if attachment is None:
            raise VoiceTranscriptionError("voice_attachment_missing", retryable=False)
        message.text = transcript
        metadata = dict(attachment.media_metadata)
        metadata["transcription_status"] = "succeeded"
        metadata["transcription_provider"] = result.provider
        metadata["transcription_model_id"] = result.model_id
        if result.provider_request_id is not None:
            metadata["transcription_provider_request_id"] = result.provider_request_id
        attachment.media_metadata = metadata
        await db.commit()
    return True
