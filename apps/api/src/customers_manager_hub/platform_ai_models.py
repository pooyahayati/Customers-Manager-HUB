from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from customers_manager_hub.models import Base


class PlatformAIProvider(StrEnum):
    OPENAI = "openai"
    GEMINI = "gemini"


class PlatformAIProviderCredential(Base):
    __tablename__ = "platform_ai_provider_credentials"
    __table_args__ = (
        CheckConstraint("provider IN ('openai', 'gemini')", name="ck_platform_ai_provider"),
    )

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("platform_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_succeeded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PlatformAITaskProfile(Base):
    __tablename__ = "platform_ai_task_profiles"
    __table_args__ = (
        CheckConstraint(
            "task_type IN ('customer_response', 'voice_transcription', "
            "'intent_classification', 'conversation_summary', "
            "'customer_memory_extraction', 'embedding')",
            name="ck_platform_ai_task_profiles_task",
        ),
        CheckConstraint(
            "timeout_seconds >= 1 AND timeout_seconds <= 120",
            name="ck_platform_ai_task_profiles_timeout",
        ),
        CheckConstraint(
            "attempts_per_route >= 1 AND attempts_per_route <= 3",
            name="ck_platform_ai_task_profiles_attempts",
        ),
        UniqueConstraint("task_type", name="uq_platform_ai_task_profiles_task"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    attempts_per_route: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
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


class PlatformAITaskRoute(Base):
    __tablename__ = "platform_ai_task_routes"
    __table_args__ = (
        CheckConstraint("provider IN ('openai', 'gemini')", name="ck_platform_ai_route_provider"),
        CheckConstraint("priority >= 0 AND priority <= 9", name="ck_platform_ai_route_priority"),
        UniqueConstraint("profile_id", "priority", name="uq_platform_ai_route_priority"),
        Index("ix_platform_ai_routes_profile", "profile_id", "priority"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    profile_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("platform_ai_task_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    parameters: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
