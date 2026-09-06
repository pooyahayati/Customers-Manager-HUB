from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from customers_manager_hub.models import Base


class AITaskType(StrEnum):
    CUSTOMER_RESPONSE = "customer_response"
    VOICE_TRANSCRIPTION = "voice_transcription"
    INTENT_CLASSIFICATION = "intent_classification"
    CONVERSATION_SUMMARY = "conversation_summary"
    CUSTOMER_MEMORY_EXTRACTION = "customer_memory_extraction"
    EMBEDDING = "embedding"


class AIExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


AI_TASK_VALUES = tuple(task.value for task in AITaskType)


class AITaskProfile(Base):
    __tablename__ = "ai_task_profiles"
    __table_args__ = (
        CheckConstraint(
            "task_type IN ('customer_response', 'voice_transcription', "
            "'intent_classification', 'conversation_summary', "
            "'customer_memory_extraction', 'embedding')",
            name="ck_ai_task_profiles_task_type",
        ),
        CheckConstraint(
            "timeout_seconds >= 1 AND timeout_seconds <= 120",
            name="ck_ai_task_profiles_timeout",
        ),
        CheckConstraint(
            "attempts_per_route >= 1 AND attempts_per_route <= 3",
            name="ck_ai_task_profiles_attempts",
        ),
        UniqueConstraint("tenant_id", "task_type", name="uq_ai_task_profiles_tenant_task"),
        UniqueConstraint("id", "tenant_id", name="uq_ai_task_profiles_id_tenant"),
        Index("ix_ai_task_profiles_tenant_id", "tenant_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    attempts_per_route: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AITaskRoute(Base):
    __tablename__ = "ai_task_routes"
    __table_args__ = (
        CheckConstraint("priority >= 0 AND priority <= 9", name="ck_ai_task_routes_priority"),
        UniqueConstraint("profile_id", "priority", name="uq_ai_task_routes_profile_priority"),
        ForeignKeyConstraint(
            ["profile_id", "tenant_id"],
            ["ai_task_profiles.id", "ai_task_profiles.tenant_id"],
            ondelete="CASCADE",
            name="fk_ai_task_routes_profile_tenant",
        ),
        Index("ix_ai_task_routes_tenant_profile", "tenant_id", "profile_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    profile_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    parameters: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AIExecutionTrace(Base):
    __tablename__ = "ai_execution_traces"
    __table_args__ = (
        CheckConstraint(
            "task_type IN ('customer_response', 'voice_transcription', "
            "'intent_classification', 'conversation_summary', "
            "'customer_memory_extraction', 'embedding')",
            name="ck_ai_execution_traces_task_type",
        ),
        CheckConstraint(
            "status IN ('succeeded', 'failed')",
            name="ck_ai_execution_traces_status",
        ),
        CheckConstraint("route_priority >= 0", name="ck_ai_execution_traces_route_priority"),
        CheckConstraint("attempt_number >= 1", name="ck_ai_execution_traces_attempt_number"),
        CheckConstraint("latency_ms >= 0", name="ck_ai_execution_traces_latency"),
        Index("ix_ai_execution_traces_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_profile_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    route_priority: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
