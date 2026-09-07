"""Deterministic tenant policy engine.

Revision ID: 0011_policy_engine
Revises: 0010_customer_memory
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_policy_engine"
down_revision: str | None = "0010_customer_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_policies",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("timezone", sa.String(length=100), nullable=False),
        sa.Column(
            "business_hours",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("outside_business_hours_action", sa.String(length=24), nullable=False),
        sa.Column("autonomy_mode", sa.String(length=24), nullable=False),
        sa.Column(
            "message_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "handoff_keywords",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("handoff_on_tool_approval", sa.Boolean(), nullable=False),
        sa.Column("approval_min_risk", sa.String(length=16), nullable=False),
        sa.Column("require_approval_for_writes", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("revision >= 1", name="ck_tenant_policies_revision"),
        sa.CheckConstraint(
            "outside_business_hours_action IN ('allow', 'handoff', 'human_only')",
            name="ck_tenant_policies_outside_hours_action",
        ),
        sa.CheckConstraint(
            "autonomy_mode IN ('autonomous', 'assist_only', 'human_only')",
            name="ck_tenant_policies_autonomy_mode",
        ),
        sa.CheckConstraint(
            "approval_min_risk IN ('low', 'medium', 'high', 'critical')",
            name="ck_tenant_policies_approval_min_risk",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )

    op.create_table(
        "tool_policy_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("effect", sa.String(length=16), nullable=False),
        sa.Column("approval_mode", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("effect IN ('allow', 'deny')", name="ck_tool_policy_rules_effect"),
        sa.CheckConstraint(
            "approval_mode IN ('inherit', 'required')",
            name="ck_tool_policy_rules_approval_mode",
        ),
        sa.ForeignKeyConstraint(
            ["tool_id", "tenant_id"],
            ["tool_definitions.id", "tool_definitions.tenant_id"],
            ondelete="CASCADE",
            name="fk_tool_policy_rules_tool_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "tool_id", name="uq_tool_policy_rules_tenant_tool"),
    )
    op.create_index(
        "ix_tool_policy_rules_tenant_tool",
        "tool_policy_rules",
        ["tenant_id", "tool_id"],
    )

    op.create_table(
        "policy_decision_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("decision_type", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "safe_context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("policy_revision >= 0", name="ck_policy_decision_traces_revision"),
        sa.CheckConstraint(
            "decision_type IN ('message', 'tool', 'autonomy', 'business_hours', 'handoff')",
            name="ck_policy_decision_traces_type",
        ),
        sa.CheckConstraint(
            "action IN ('allow', 'deny', 'approval_required', 'handoff')",
            name="ck_policy_decision_traces_action",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_policy_decision_traces_tenant_created",
        "policy_decision_traces",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_policy_decision_traces_tenant_conversation",
        "policy_decision_traces",
        ["tenant_id", "conversation_id", "created_at"],
    )
    op.create_index(
        "ix_policy_decision_traces_tenant_tool",
        "policy_decision_traces",
        ["tenant_id", "tool_id", "created_at"],
    )

    op.execute(
        """
        INSERT INTO tenant_policies (
            tenant_id,
            enabled,
            revision,
            timezone,
            business_hours,
            outside_business_hours_action,
            autonomy_mode,
            message_rules,
            handoff_keywords,
            handoff_on_tool_approval,
            approval_min_risk,
            require_approval_for_writes
        )
        SELECT
            tenant_id,
            enabled,
            1,
            'UTC',
            '{}'::jsonb,
            'allow',
            'autonomous',
            '{}'::jsonb,
            customer_keywords,
            pause_on_tool_approval,
            'high',
            false
        FROM handoff_policies
        ON CONFLICT (tenant_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_policy_decision_traces_tenant_tool", table_name="policy_decision_traces")
    op.drop_index(
        "ix_policy_decision_traces_tenant_conversation", table_name="policy_decision_traces"
    )
    op.drop_index("ix_policy_decision_traces_tenant_created", table_name="policy_decision_traces")
    op.drop_table("policy_decision_traces")
    op.drop_index("ix_tool_policy_rules_tenant_tool", table_name="tool_policy_rules")
    op.drop_table("tool_policy_rules")
    op.drop_table("tenant_policies")
