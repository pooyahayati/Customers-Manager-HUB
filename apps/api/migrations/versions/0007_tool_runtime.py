"""Tool runtime, credentials, permissions, and execution traces.

Revision ID: 0007_tool_runtime
Revises: 0006_website_chat_channel
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_tool_runtime"
down_revision: str | None = "0006_website_chat_channel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("adapter_kind", sa.String(length=32), nullable=False),
        sa.Column("operation_type", sa.String(length=16), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("input_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("configuration", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "adapter_kind IN ('generic_rest', 'business_reference', 'google_sheets')",
            name="ck_tool_definitions_adapter_kind",
        ),
        sa.CheckConstraint(
            "operation_type IN ('read', 'write')",
            name="ck_tool_definitions_operation_type",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_tool_definitions_risk_level",
        ),
        sa.CheckConstraint("version >= 1", name="ck_tool_definitions_version"),
        sa.CheckConstraint(
            "timeout_seconds >= 1 AND timeout_seconds <= 60",
            name="ck_tool_definitions_timeout",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 3",
            name="ck_tool_definitions_attempts",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_tool_definitions_id_tenant"),
        sa.UniqueConstraint(
            "tenant_id",
            "name",
            "version",
            name="uq_tool_definitions_tenant_name_version",
        ),
    )
    op.create_index(
        "ix_tool_definitions_tenant_active",
        "tool_definitions",
        ["tenant_id", "is_active", "created_at"],
    )

    op.create_table(
        "tool_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auth_type", sa.String(length=16), nullable=False),
        sa.Column("header_name", sa.String(length=100), nullable=True),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "auth_type IN ('bearer', 'header')",
            name="ck_tool_credentials_auth_type",
        ),
        sa.ForeignKeyConstraint(
            ["tool_id", "tenant_id"],
            ["tool_definitions.id", "tool_definitions.tenant_id"],
            ondelete="CASCADE",
            name="fk_tool_credentials_tool_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tool_id", name="uq_tool_credentials_tool"),
    )
    op.create_index(
        "ix_tool_credentials_tenant_tool",
        "tool_credentials",
        ["tenant_id", "tool_id"],
    )

    op.create_table(
        "agent_tool_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_id", "tenant_id"],
            ["agents.id", "agents.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_tool_permissions_agent_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tool_id", "tenant_id"],
            ["tool_definitions.id", "tool_definitions.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_tool_permissions_tool_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_id",
            "tool_id",
            name="uq_agent_tool_permissions_agent_tool",
        ),
    )
    op.create_index(
        "ix_agent_tool_permissions_tenant_agent",
        "agent_tool_permissions",
        ["tenant_id", "agent_id"],
    )
    op.create_index(
        "ix_agent_tool_permissions_tenant_tool",
        "agent_tool_permissions",
        ["tenant_id", "tool_id"],
    )

    op.create_table(
        "tool_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("call_ordinal", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("approval_status", sa.String(length=32), nullable=False),
        sa.Column("input_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approval_required', 'running', 'succeeded', 'failed', 'denied')",
            name="ck_tool_executions_status",
        ),
        sa.CheckConstraint(
            "approval_status IN ('not_required', 'pending', 'approved', 'denied')",
            name="ck_tool_executions_approval_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_tool_executions_attempt_count"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            ["platform_users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id", "tenant_id"],
            ["agents.id", "agents.tenant_id"],
            ondelete="RESTRICT",
            name="fk_tool_executions_agent_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tool_id", "tenant_id"],
            ["tool_definitions.id", "tool_definitions.tenant_id"],
            ondelete="RESTRICT",
            name="fk_tool_executions_tool_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_tool_executions_tenant_idempotency",
        ),
    )
    op.create_index(
        "uq_tool_executions_agent_run_ordinal",
        "tool_executions",
        ["agent_run_id", "call_ordinal"],
        unique=True,
        postgresql_where=sa.text("agent_run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_tool_executions_tenant_created",
        "tool_executions",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_tool_executions_tenant_status",
        "tool_executions",
        ["tenant_id", "status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tool_executions_tenant_status", table_name="tool_executions")
    op.drop_index("ix_tool_executions_tenant_created", table_name="tool_executions")
    op.drop_index("uq_tool_executions_agent_run_ordinal", table_name="tool_executions")
    op.drop_table("tool_executions")
    op.drop_index("ix_agent_tool_permissions_tenant_tool", table_name="agent_tool_permissions")
    op.drop_index("ix_agent_tool_permissions_tenant_agent", table_name="agent_tool_permissions")
    op.drop_table("agent_tool_permissions")
    op.drop_index("ix_tool_credentials_tenant_tool", table_name="tool_credentials")
    op.drop_table("tool_credentials")
    op.drop_index("ix_tool_definitions_tenant_active", table_name="tool_definitions")
    op.drop_table("tool_definitions")
