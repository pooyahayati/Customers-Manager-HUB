"""Human handoff, operator inbox policy, and assist suggestions.

Revision ID: 0009_human_handoff
Revises: 0008_knowledge_rag
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_human_handoff"
down_revision: str | None = "0008_knowledge_rag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_agent_runs_status", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_status",
        "agent_runs",
        "status IN ('pending', 'generated', 'paused', 'succeeded', 'failed')",
    )

    op.create_table(
        "conversation_handoffs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("request_source", sa.String(length=24), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("reason_text", sa.String(length=1000), nullable=True),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claimed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_tool_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'claimed', 'resolved', 'cancelled')",
            name="ck_conversation_handoffs_status",
        ),
        sa.CheckConstraint(
            "request_source IN ('human', 'system', 'tool')",
            name="ck_conversation_handoffs_request_source",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["platform_users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["claimed_by_user_id"], ["platform_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["source_tool_execution_id"], ["tool_executions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_conversation_handoffs_id_tenant"),
    )
    op.create_index(
        "uq_conversation_handoffs_one_active",
        "conversation_handoffs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'claimed')"),
    )
    op.create_index(
        "ix_conversation_handoffs_tenant_status_created",
        "conversation_handoffs",
        ["tenant_id", "status", "created_at"],
    )
    op.create_index(
        "ix_conversation_handoffs_tenant_claimant",
        "conversation_handoffs",
        ["tenant_id", "claimed_by_user_id", "updated_at"],
    )

    op.create_table(
        "handoff_policies",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "customer_keywords",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("pause_on_tool_approval", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )

    op.create_table(
        "operator_assist_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("handoff_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["handoff_id", "tenant_id"],
            ["conversation_handoffs.id", "conversation_handoffs.tenant_id"],
            ondelete="CASCADE",
            name="fk_operator_assist_handoff_tenant",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["platform_users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_operator_assist_tenant_conversation_created",
        "operator_assist_suggestions",
        ["tenant_id", "conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_operator_assist_tenant_conversation_created",
        table_name="operator_assist_suggestions",
    )
    op.drop_table("operator_assist_suggestions")
    op.drop_table("handoff_policies")
    op.drop_index("ix_conversation_handoffs_tenant_claimant", table_name="conversation_handoffs")
    op.drop_index(
        "ix_conversation_handoffs_tenant_status_created", table_name="conversation_handoffs"
    )
    op.drop_index("uq_conversation_handoffs_one_active", table_name="conversation_handoffs")
    op.drop_table("conversation_handoffs")

    op.drop_constraint("ck_agent_runs_status", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_status",
        "agent_runs",
        "status IN ('pending', 'generated', 'succeeded', 'failed')",
    )
