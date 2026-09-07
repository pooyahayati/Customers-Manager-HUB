"""Add effective-dated tenant AI pricing.

Revision ID: 0012_analytics_usage_cost
Revises: 0011_policy_engine
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_analytics_usage_cost"
down_revision: str | None = "0011_policy_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_model_pricing",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("input_per_million_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("output_per_million_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("audio_per_minute_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "input_per_million_usd >= 0",
            name="ck_ai_model_pricing_input_nonnegative",
        ),
        sa.CheckConstraint(
            "output_per_million_usd >= 0",
            name="ck_ai_model_pricing_output_nonnegative",
        ),
        sa.CheckConstraint(
            "audio_per_minute_usd >= 0",
            name="ck_ai_model_pricing_audio_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["platform_users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "model_id",
            "effective_from",
            name="uq_ai_model_pricing_tenant_provider_model_effective",
        ),
    )
    op.create_index(
        "ix_ai_model_pricing_tenant_provider_model_effective",
        "ai_model_pricing",
        ["tenant_id", "provider", "model_id", "effective_from"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_model_pricing_tenant_provider_model_effective",
        table_name="ai_model_pricing",
    )
    op.drop_table("ai_model_pricing")
