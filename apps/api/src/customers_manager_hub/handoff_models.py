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
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from customers_manager_hub.models import Base


class HandoffStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class HandoffRequestSource(StrEnum):
    HUMAN = "human"
    SYSTEM = "system"
    TOOL = "tool"


class ConversationHandoff(Base):
    __tablename__ = "conversation_handoffs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'claimed', 'resolved', 'cancelled')",
            name="ck_conversation_handoffs_status",
        ),
        CheckConstraint(
            "request_source IN ('human', 'system', 'tool')",
            name="ck_conversation_handoffs_request_source",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_conversation_handoffs_id_tenant"),
        Index(
            "uq_conversation_handoffs_one_active",
            "conversation_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'claimed')"),
        ),
        Index(
            "ix_conversation_handoffs_tenant_status_created",
            "tenant_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_conversation_handoffs_tenant_claimant",
            "tenant_id",
            "claimed_by_user_id",
            "updated_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=HandoffStatus.QUEUED.value
    )
    request_source: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    reason_text: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("platform_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    claimed_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("platform_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_agent_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_tool_execution_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tool_executions.id", ondelete="SET NULL"),
        nullable=True,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class HandoffPolicy(Base):
    __tablename__ = "handoff_policies"

    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    customer_keywords: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    pause_on_tool_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class OperatorAssistSuggestion(Base):
    __tablename__ = "operator_assist_suggestions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["handoff_id", "tenant_id"],
            ["conversation_handoffs.id", "conversation_handoffs.tenant_id"],
            ondelete="CASCADE",
            name="fk_operator_assist_handoff_tenant",
        ),
        Index(
            "ix_operator_assist_tenant_conversation_created",
            "tenant_id",
            "conversation_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    handoff_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("platform_users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
