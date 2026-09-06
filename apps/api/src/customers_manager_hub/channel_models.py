from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from customers_manager_hub.models import Base


class ChannelType(StrEnum):
    TELEGRAM = "telegram"


class ChannelCapability(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    OUTBOUND_TEXT = "outbound_text"


class ChannelCredentialKind(StrEnum):
    TELEGRAM_BOT_TOKEN = "telegram_bot_token"
    TELEGRAM_WEBHOOK_SECRET = "telegram_webhook_secret"


class ChannelInboundEventStatus(StrEnum):
    RECEIVED = "received"
    ENQUEUED = "enqueued"
    PROCESSED = "processed"
    IGNORED = "ignored"


class ChannelAccount(Base):
    __tablename__ = "channel_accounts"
    __table_args__ = (
        CheckConstraint("channel_type IN ('telegram')", name="ck_channel_accounts_type"),
        UniqueConstraint("id", "tenant_id", name="uq_channel_accounts_id_tenant"),
        UniqueConstraint(
            "channel_type",
            "external_account_id",
            name="uq_channel_accounts_type_external",
        ),
        Index("ix_channel_accounts_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ChannelCredential(Base):
    __tablename__ = "channel_credentials"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('telegram_bot_token', 'telegram_webhook_secret')",
            name="ck_channel_credentials_kind",
        ),
        UniqueConstraint(
            "channel_account_id",
            "kind",
            name="uq_channel_credentials_account_kind",
        ),
        ForeignKeyConstraint(
            ["channel_account_id", "tenant_id"],
            ["channel_accounts.id", "channel_accounts.tenant_id"],
            ondelete="CASCADE",
            name="fk_channel_credentials_account_tenant",
        ),
        Index("ix_channel_credentials_tenant_account", "tenant_id", "channel_account_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    channel_account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ChannelAccountCapability(Base):
    __tablename__ = "channel_account_capabilities"
    __table_args__ = (
        CheckConstraint(
            "capability IN ('text', 'voice')",
            name="ck_channel_account_capabilities_value",
        ),
        ForeignKeyConstraint(
            ["channel_account_id", "tenant_id"],
            ["channel_accounts.id", "channel_accounts.tenant_id"],
            ondelete="CASCADE",
            name="fk_channel_account_capabilities_account_tenant",
        ),
        Index(
            "ix_channel_account_capabilities_tenant_account",
            "tenant_id",
            "channel_account_id",
        ),
    )

    channel_account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    capability: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ConversationChannelBinding(Base):
    __tablename__ = "conversation_channel_bindings"
    __table_args__ = (
        UniqueConstraint(
            "channel_account_id",
            "external_thread_id",
            name="uq_conversation_channel_bindings_account_thread",
        ),
        UniqueConstraint(
            "conversation_id",
            name="uq_conversation_channel_bindings_conversation",
        ),
        ForeignKeyConstraint(
            ["channel_account_id", "tenant_id"],
            ["channel_accounts.id", "channel_accounts.tenant_id"],
            ondelete="CASCADE",
            name="fk_conversation_channel_bindings_account_tenant",
        ),
        Index(
            "ix_conversation_channel_bindings_tenant_account",
            "tenant_id",
            "channel_account_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel_account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    external_thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChannelInboundEvent(Base):
    __tablename__ = "channel_inbound_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('received', 'enqueued', 'processed', 'ignored')",
            name="ck_channel_inbound_events_status",
        ),
        UniqueConstraint(
            "channel_account_id",
            "external_event_id",
            name="uq_channel_inbound_events_account_external",
        ),
        ForeignKeyConstraint(
            ["channel_account_id", "tenant_id"],
            ["channel_accounts.id", "channel_accounts.tenant_id"],
            ondelete="CASCADE",
            name="fk_channel_inbound_events_account_tenant",
        ),
        Index("ix_channel_inbound_events_tenant_created", "tenant_id", "created_at"),
        Index("ix_channel_inbound_events_status_updated", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    channel_account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    message_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
