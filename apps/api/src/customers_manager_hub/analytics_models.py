from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from customers_manager_hub.models import Base


class AIModelPricing(Base):
    __tablename__ = "ai_model_pricing"
    __table_args__ = (
        CheckConstraint(
            "input_per_million_usd >= 0",
            name="ck_ai_model_pricing_input_nonnegative",
        ),
        CheckConstraint(
            "output_per_million_usd >= 0",
            name="ck_ai_model_pricing_output_nonnegative",
        ),
        CheckConstraint(
            "audio_per_minute_usd >= 0",
            name="ck_ai_model_pricing_audio_nonnegative",
        ),
        UniqueConstraint(
            "tenant_id",
            "provider",
            "model_id",
            "effective_from",
            name="uq_ai_model_pricing_tenant_provider_model_effective",
        ),
        Index(
            "ix_ai_model_pricing_tenant_provider_model_effective",
            "tenant_id",
            "provider",
            "model_id",
            "effective_from",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    input_per_million_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=Decimal("0")
    )
    output_per_million_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=Decimal("0")
    )
    audio_per_minute_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=Decimal("0")
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("platform_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
