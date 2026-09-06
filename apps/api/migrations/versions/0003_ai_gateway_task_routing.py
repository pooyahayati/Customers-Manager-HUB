"""AI task profiles, routes, and execution traces.

Revision ID: 0003_ai_gateway_task_routing
Revises: 0002_customer_conversation
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_ai_gateway_task_routing"
down_revision: str | None = "0002_customer_conversation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TASK_CHECK = (
    "task_type IN ('customer_response', 'voice_transcription', "
    "'intent_classification', 'conversation_summary', "
    "'customer_memory_extraction', 'embedding')"
)


def upgrade() -> None:
    op.create_table(
        "ai_task_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("attempts_per_route", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(_TASK_CHECK, name="ck_ai_task_profiles_task_type"),
        sa.CheckConstraint(
            "timeout_seconds >= 1 AND timeout_seconds <= 120",
            name="ck_ai_task_profiles_timeout",
        ),
        sa.CheckConstraint(
            "attempts_per_route >= 1 AND attempts_per_route <= 3",
            name="ck_ai_task_profiles_attempts",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_ai_task_profiles_id_tenant"),
        sa.UniqueConstraint("tenant_id", "task_type", name="uq_ai_task_profiles_tenant_task"),
    )
    op.create_index(
        "ix_ai_task_profiles_tenant_id",
        "ai_task_profiles",
        ["tenant_id"],
        unique=False,
    )

    op.create_table(
        "ai_task_routes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("priority >= 0 AND priority <= 9", name="ck_ai_task_routes_priority"),
        sa.ForeignKeyConstraint(
            ["profile_id", "tenant_id"],
            ["ai_task_profiles.id", "ai_task_profiles.tenant_id"],
            ondelete="CASCADE",
            name="fk_ai_task_routes_profile_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "priority", name="uq_ai_task_routes_profile_priority"),
    )
    op.create_index(
        "ix_ai_task_routes_tenant_profile",
        "ai_task_routes",
        ["tenant_id", "profile_id"],
        unique=False,
    )

    op.create_table(
        "ai_execution_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("route_priority", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("audio_seconds", sa.Float(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(_TASK_CHECK, name="ck_ai_execution_traces_task_type"),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed')",
            name="ck_ai_execution_traces_status",
        ),
        sa.CheckConstraint("route_priority >= 0", name="ck_ai_execution_traces_route_priority"),
        sa.CheckConstraint("attempt_number >= 1", name="ck_ai_execution_traces_attempt_number"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_ai_execution_traces_latency"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_execution_traces_tenant_created",
        "ai_execution_traces",
        ["tenant_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_execution_traces_tenant_created", table_name="ai_execution_traces")
    op.drop_table("ai_execution_traces")
    op.drop_index("ix_ai_task_routes_tenant_profile", table_name="ai_task_routes")
    op.drop_table("ai_task_routes")
    op.drop_index("ix_ai_task_profiles_tenant_id", table_name="ai_task_profiles")
    op.drop_table("ai_task_profiles")
