from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from customers_manager_hub.channel_models import ChannelCapability, ChannelType
from customers_manager_hub.models import MessageType


class ChannelProviderError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class CanonicalAttachment:
    media_type: MessageType
    mime_type: str | None = None
    filename: str | None = None
    size_bytes: int | None = None
    external_media_id: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CanonicalInboundMessage:
    channel_type: ChannelType
    channel_account_id: UUID
    external_event_id: str
    external_message_id: str
    external_thread_id: str
    sender_namespace: str
    sender_external_id: str
    sender_display_name: str | None
    message_type: MessageType
    text: str | None
    occurred_at: datetime
    attachments: tuple[CanonicalAttachment, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChannelAccountIdentity:
    external_account_id: str
    username: str | None
    display_name: str


@dataclass(frozen=True, slots=True)
class ChannelMediaReference:
    external_media_id: str
    external_unique_id: str
    file_path: str | None
    size_bytes: int | None


@dataclass(frozen=True, slots=True)
class ChannelSendResult:
    external_message_id: str
    occurred_at: datetime


class ChannelAdapter(Protocol):
    @property
    def channel_type(self) -> ChannelType: ...

    @property
    def capabilities(self) -> frozenset[ChannelCapability]: ...

    async def validate_account(self, access_secret: str) -> ChannelAccountIdentity: ...

    async def register_webhook(
        self,
        access_secret: str,
        *,
        webhook_url: str,
        webhook_secret: str,
    ) -> None: ...

    async def resolve_media(
        self,
        access_secret: str,
        external_media_id: str,
    ) -> ChannelMediaReference: ...

    async def send_text(
        self,
        access_secret: str,
        *,
        external_thread_id: str,
        text: str,
    ) -> ChannelSendResult: ...


class ChannelRegistry:
    def __init__(self, adapters: tuple[ChannelAdapter, ...] = ()) -> None:
        self._adapters: dict[ChannelType, ChannelAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ChannelAdapter) -> None:
        if adapter.channel_type in self._adapters:
            raise ValueError(f"Channel adapter is already registered: {adapter.channel_type.value}")
        self._adapters[adapter.channel_type] = adapter

    def get(self, channel_type: ChannelType) -> ChannelAdapter:
        adapter = self._adapters.get(channel_type)
        if adapter is None:
            raise LookupError(f"Channel adapter is not registered: {channel_type.value}")
        return adapter
