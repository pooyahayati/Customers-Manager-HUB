"""Agent, versioned prompts, assignments, and durable runs.

Revision ID: 0005_agent_prompt_runtime
Revises: 0004_channel_framework_telegram
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_agent_prompt_runtime"
down_revision: str | None = "0004_channel_framework_telegram"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_prompts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_agent_prompts_id_tenant"),
    )
    op.create_index(
        "ix_agent_prompts_tenant_created",
        "agent_prompts",
        ["tenant_id", "created_at"],
    )

    op.create_table(
        "agent_prompt_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prompt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("version >= 1", name="ck_agent_prompt_versions_version"),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_agent_prompt_versions_status",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_id", "tenant_id"],
            ["agent_prompts.id", "agent_prompts.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_prompt_versions_prompt_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_agent_prompt_versions_id_tenant"),
        sa.UniqueConstraint(
            "prompt_id",
            "version",
            name="uq_agent_prompt_versions_prompt_version",
        ),
    )
    op.create_index(
        "uq_agent_prompt_versions_one_draft",
        "agent_prompt_versions",
        ["prompt_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )
    op.create_index(
        "uq_agent_prompt_versions_one_published",
        "agent_prompt_versions",
        ["prompt_id"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_index(
        "ix_agent_prompt_versions_tenant_prompt",
        "agent_prompt_versions",
        ["tenant_id", "prompt_id", "version"],
    )

    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prompt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["prompt_id", "tenant_id"],
            ["agent_prompts.id", "agent_prompts.tenant_id"],
            ondelete="RESTRICT",
            name="fk_agents_prompt_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_agents_id_tenant"),
    )
    op.create_index("ix_agents_tenant_created", "agents", ["tenant_id", "created_at"])
    op.create_index("ix_agents_tenant_prompt", "agents", ["tenant_id", "prompt_id"])

    op.create_table(
        "agent_channel_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_account_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["agent_id", "tenant_id"],
            ["agents.id", "agents.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_channel_assignments_agent_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["channel_account_id", "tenant_id"],
            ["channel_accounts.id", "channel_accounts.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_channel_assignments_channel_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_account_id",
            name="uq_agent_channel_assignments_channel_account",
        ),
    )
    op.create_index(
        "ix_agent_channel_assignments_tenant_agent",
        "agent_channel_assignments",
        ["tenant_id", "agent_id"],
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prompt_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inbound_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outbound_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("generated_text", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('pending', 'generated', 'succeeded', 'failed')",
            name="ck_agent_runs_status",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["agent_id", "tenant_id"],
            ["agents.id", "agents.tenant_id"],
            ondelete="RESTRICT",
            name="fk_agent_runs_agent_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_version_id", "tenant_id"],
            ["agent_prompt_versions.id", "agent_prompt_versions.tenant_id"],
            ondelete="RESTRICT",
            name="fk_agent_runs_prompt_version_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["channel_account_id", "tenant_id"],
            ["channel_accounts.id", "channel_accounts.tenant_id"],
            ondelete="RESTRICT",
            name="fk_agent_runs_channel_tenant",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["inbound_message_id"], ["messages.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["outbound_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "inbound_message_id",
            name="uq_agent_runs_tenant_inbound_message",
        ),
    )
    op.create_index(
        "ix_agent_runs_tenant_status",
        "agent_runs",
        ["tenant_id", "status", "updated_at"],
    )
    op.create_index("ix_agent_runs_lease", "agent_runs", ["lease_until"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_lease", table_name="agent_runs")
    op.drop_index("ix_agent_runs_tenant_status", table_name="agent_runs")
    op.drop_table("agent_runs")

    op.drop_index(
        "ix_agent_channel_assignments_tenant_agent",
        table_name="agent_channel_assignments",
    )
    op.drop_table("agent_channel_assignments")

    op.drop_index("ix_agents_tenant_prompt", table_name="agents")
    op.drop_index("ix_agents_tenant_created", table_name="agents")
    op.drop_table("agents")

    op.drop_index(
        "ix_agent_prompt_versions_tenant_prompt",
        table_name="agent_prompt_versions",
    )
    op.drop_index(
        "uq_agent_prompt_versions_one_published",
        table_name="agent_prompt_versions",
    )
    op.drop_index(
        "uq_agent_prompt_versions_one_draft",
        table_name="agent_prompt_versions",
    )
    op.drop_table("agent_prompt_versions")

    op.drop_index("ix_agent_prompts_tenant_created", table_name="agent_prompts")
    op.drop_table("agent_prompts")
