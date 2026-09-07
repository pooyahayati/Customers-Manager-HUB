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
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from customers_manager_hub.models import Base


class ToolAdapterKind(StrEnum):
    GENERIC_REST = "generic_rest"
    BUSINESS_REFERENCE = "business_reference"
    GOOGLE_SHEETS = "google_sheets"


class ToolOperationType(StrEnum):
    READ = "read"
    WRITE = "write"


class ToolRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ToolAuthType(StrEnum):
    BEARER = "bearer"
    HEADER = "header"


class ToolExecutionStatus(StrEnum):
    PENDING = "pending"
    APPROVAL_REQUIRED = "approval_required"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"


class ToolApprovalStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class ToolDefinition(Base):
    __tablename__ = "tool_definitions"
    __table_args__ = (
        CheckConstraint(
            "adapter_kind IN ('generic_rest', 'business_reference', 'google_sheets')",
            name="ck_tool_definitions_adapter_kind",
        ),
        CheckConstraint(
            "operation_type IN ('read', 'write')",
            name="ck_tool_definitions_operation_type",
        ),
        CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_tool_definitions_risk_level",
        ),
        CheckConstraint("version >= 1", name="ck_tool_definitions_version"),
        CheckConstraint(
            "timeout_seconds >= 1 AND timeout_seconds <= 60",
            name="ck_tool_definitions_timeout",
        ),
        CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 3",
            name="ck_tool_definitions_attempts",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_tool_definitions_id_tenant"),
        UniqueConstraint(
            "tenant_id",
            "name",
            "version",
            name="uq_tool_definitions_tenant_name_version",
        ),
        Index("ix_tool_definitions_tenant_active", "tenant_id", "is_active", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    description: Mapped[str] = mapped_column(String(1000), nullable=False)
    adapter_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    input_schema: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    output_schema: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ToolCredential(Base):
    __tablename__ = "tool_credentials"
    __table_args__ = (
        CheckConstraint(
            "auth_type IN ('bearer', 'header')",
            name="ck_tool_credentials_auth_type",
        ),
        UniqueConstraint("tool_id", name="uq_tool_credentials_tool"),
        ForeignKeyConstraint(
            ["tool_id", "tenant_id"],
            ["tool_definitions.id", "tool_definitions.tenant_id"],
            ondelete="CASCADE",
            name="fk_tool_credentials_tool_tenant",
        ),
        Index("ix_tool_credentials_tenant_tool", "tenant_id", "tool_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tool_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(16), nullable=False)
    header_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AgentToolPermission(Base):
    __tablename__ = "agent_tool_permissions"
    __table_args__ = (
        UniqueConstraint(
            "agent_id",
            "tool_id",
            name="uq_agent_tool_permissions_agent_tool",
        ),
        ForeignKeyConstraint(
            ["agent_id", "tenant_id"],
            ["agents.id", "agents.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_tool_permissions_agent_tenant",
        ),
        ForeignKeyConstraint(
            ["tool_id", "tenant_id"],
            ["tool_definitions.id", "tool_definitions.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_tool_permissions_tool_tenant",
        ),
        Index("ix_agent_tool_permissions_tenant_agent", "tenant_id", "agent_id"),
        Index("ix_agent_tool_permissions_tenant_tool", "tenant_id", "tool_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    agent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tool_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approval_required', 'running', 'succeeded', 'failed', 'denied')",
            name="ck_tool_executions_status",
        ),
        CheckConstraint(
            "approval_status IN ('not_required', 'pending', 'approved', 'denied')",
            name="ck_tool_executions_approval_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_tool_executions_attempt_count"),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_tool_executions_tenant_idempotency",
        ),
        ForeignKeyConstraint(
            ["tool_id", "tenant_id"],
            ["tool_definitions.id", "tool_definitions.tenant_id"],
            ondelete="RESTRICT",
            name="fk_tool_executions_tool_tenant",
        ),
        ForeignKeyConstraint(
            ["agent_id", "tenant_id"],
            ["agents.id", "agents.tenant_id"],
            ondelete="RESTRICT",
            name="fk_tool_executions_agent_tenant",
        ),
        Index(
            "uq_tool_executions_agent_run_ordinal",
            "agent_run_id",
            "call_ordinal",
            unique=True,
            postgresql_where=text("agent_run_id IS NOT NULL"),
        ),
        Index("ix_tool_executions_tenant_created", "tenant_id", "created_at"),
        Index("ix_tool_executions_tenant_status", "tenant_id", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    agent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    agent_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    call_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ToolExecutionStatus.PENDING.value
    )
    approval_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ToolApprovalStatus.NOT_REQUIRED.value
    )
    input_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    output_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("platform_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
