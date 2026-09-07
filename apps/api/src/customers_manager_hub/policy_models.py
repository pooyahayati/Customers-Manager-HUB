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
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from customers_manager_hub.models import Base


class PolicyAutonomyMode(StrEnum):
    AUTONOMOUS = "autonomous"
    ASSIST_ONLY = "assist_only"
    HUMAN_ONLY = "human_only"


class OutsideBusinessHoursAction(StrEnum):
    ALLOW = "allow"
    HANDOFF = "handoff"
    HUMAN_ONLY = "human_only"


class PolicyMessageAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    HANDOFF = "handoff"


class PolicyToolEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class PolicyApprovalMode(StrEnum):
    INHERIT = "inherit"
    REQUIRED = "required"


class PolicyDecisionType(StrEnum):
    MESSAGE = "message"
    TOOL = "tool"
    AUTONOMY = "autonomy"
    BUSINESS_HOURS = "business_hours"
    HANDOFF = "handoff"


class PolicyDecisionAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"
    HANDOFF = "handoff"


class TenantPolicy(Base):
    __tablename__ = "tenant_policies"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_tenant_policies_revision"),
        CheckConstraint(
            "outside_business_hours_action IN ('allow', 'handoff', 'human_only')",
            name="ck_tenant_policies_outside_hours_action",
        ),
        CheckConstraint(
            "autonomy_mode IN ('autonomous', 'assist_only', 'human_only')",
            name="ck_tenant_policies_autonomy_mode",
        ),
        CheckConstraint(
            "approval_min_risk IN ('low', 'medium', 'high', 'critical')",
            name="ck_tenant_policies_approval_min_risk",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="UTC")
    business_hours: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    outside_business_hours_action: Mapped[str] = mapped_column(
        String(24), nullable=False, default=OutsideBusinessHoursAction.ALLOW.value
    )
    autonomy_mode: Mapped[str] = mapped_column(
        String(24), nullable=False, default=PolicyAutonomyMode.AUTONOMOUS.value
    )
    message_rules: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    handoff_keywords: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    handoff_on_tool_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    approval_min_risk: Mapped[str] = mapped_column(String(16), nullable=False, default="high")
    require_approval_for_writes: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ToolPolicyRule(Base):
    __tablename__ = "tool_policy_rules"
    __table_args__ = (
        CheckConstraint("effect IN ('allow', 'deny')", name="ck_tool_policy_rules_effect"),
        CheckConstraint(
            "approval_mode IN ('inherit', 'required')",
            name="ck_tool_policy_rules_approval_mode",
        ),
        UniqueConstraint("tenant_id", "tool_id", name="uq_tool_policy_rules_tenant_tool"),
        ForeignKeyConstraint(
            ["tool_id", "tenant_id"],
            ["tool_definitions.id", "tool_definitions.tenant_id"],
            ondelete="CASCADE",
            name="fk_tool_policy_rules_tool_tenant",
        ),
        Index("ix_tool_policy_rules_tenant_tool", "tenant_id", "tool_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tool_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    effect: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PolicyToolEffect.ALLOW.value
    )
    approval_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PolicyApprovalMode.INHERIT.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PolicyDecisionTrace(Base):
    __tablename__ = "policy_decision_traces"
    __table_args__ = (
        CheckConstraint(
            "decision_type IN ('message', 'tool', 'autonomy', 'business_hours', 'handoff')",
            name="ck_policy_decision_traces_type",
        ),
        CheckConstraint(
            "action IN ('allow', 'deny', 'approval_required', 'handoff')",
            name="ck_policy_decision_traces_action",
        ),
        CheckConstraint("policy_revision >= 0", name="ck_policy_decision_traces_revision"),
        Index("ix_policy_decision_traces_tenant_created", "tenant_id", "created_at"),
        Index(
            "ix_policy_decision_traces_tenant_conversation",
            "tenant_id",
            "conversation_id",
            "created_at",
        ),
        Index("ix_policy_decision_traces_tenant_tool", "tenant_id", "tool_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decision_type: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    conversation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    message_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    agent_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    tool_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    safe_context: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
