from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from customers_manager_hub.models import Base


class WalletTransactionKind(StrEnum):
    CREDIT = "credit"
    AI_DEBIT = "ai_debit"


class BillingDisplayUnit(StrEnum):
    RIAL = "rial"
    TOMAN = "toman"


class PlatformBillingSettings(Base):
    __tablename__ = "platform_billing_settings"
    __table_args__ = (
        CheckConstraint(
            "display_unit IN ('rial', 'toman')",
            name="ck_platform_billing_display_unit",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    display_unit: Mapped[str] = mapped_column(
        String(16), nullable=False, default=BillingDisplayUnit.RIAL.value
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("platform_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PlatformAIModelPrice(Base):
    __tablename__ = "platform_ai_model_pricing"
    __table_args__ = (
        CheckConstraint("provider IN ('openai', 'gemini')", name="ck_platform_price_provider"),
        CheckConstraint(
            "input_per_million_rial >= 0",
            name="ck_platform_price_input_nonnegative",
        ),
        CheckConstraint(
            "output_per_million_rial >= 0",
            name="ck_platform_price_output_nonnegative",
        ),
        CheckConstraint(
            "audio_per_minute_rial >= 0",
            name="ck_platform_price_audio_nonnegative",
        ),
        UniqueConstraint(
            "provider",
            "model_id",
            "effective_from",
            name="uq_platform_price_provider_model_effective",
        ),
        Index(
            "ix_platform_price_provider_model_effective",
            "provider",
            "model_id",
            "effective_from",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    input_per_million_rial: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_per_million_rial: Mapped[int] = mapped_column(BigInteger, nullable=False)
    audio_per_minute_rial: Mapped[int] = mapped_column(BigInteger, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("platform_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BusinessWallet(Base):
    __tablename__ = "business_wallets"

    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    balance_rial: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BusinessWalletTransaction(Base):
    __tablename__ = "business_wallet_transactions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('credit', 'ai_debit')",
            name="ck_business_wallet_transaction_kind",
        ),
        CheckConstraint("amount_rial >= 0", name="ck_business_wallet_transaction_amount"),
        UniqueConstraint(
            "ai_execution_trace_id",
            name="uq_business_wallet_transaction_trace",
        ),
        Index(
            "ix_business_wallet_transactions_tenant_created",
            "tenant_id",
            "created_at",
        ),
        Index(
            "ix_business_wallet_transactions_message",
            "tenant_id",
            "message_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_rial: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_rial: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ai_execution_trace_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ai_execution_traces.id", ondelete="SET NULL"),
        nullable=True,
    )
    message_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    audio_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("platform_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
