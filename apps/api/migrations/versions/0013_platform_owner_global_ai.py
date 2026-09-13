"""Add Platform Owner and global AI administration.

Revision ID: 0013_platform_owner_global_ai
Revises: 0012_analytics_usage_cost
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_platform_owner_global_ai"
down_revision: str | None = "0012_analytics_usage_cost"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TASK_CHECK = (
    "task_type IN ('customer_response', 'voice_transcription', "
    "'intent_classification', 'conversation_summary', "
    "'customer_memory_extraction', 'embedding')"
)


def upgrade() -> None:
    op.add_column(
        "platform_users",
        sa.Column(
            "is_platform_owner",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    # Preserve the identity created by legacy installations. The original
    # bootstrap command was allowed only on an empty database and recorded its
    # actor in a tenant.bootstrap audit event, so this promotes that precise
    # account instead of every historical Business member with an owner role.
    op.execute(
        """
        UPDATE platform_users AS platform_user
        SET is_platform_owner = TRUE
        WHERE platform_user.id = (
            SELECT audit_event.actor_user_id
            FROM audit_events AS audit_event
            WHERE audit_event.action = 'tenant.bootstrap'
              AND audit_event.actor_user_id IS NOT NULL
            ORDER BY audit_event.created_at ASC, audit_event.id ASC
            LIMIT 1
        )
          AND NOT EXISTS (
              SELECT 1
              FROM platform_users AS existing_owner
              WHERE existing_owner.is_platform_owner IS TRUE
          )
        """
    )

    op.create_table(
        "platform_ai_provider_credentials",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_succeeded", sa.Boolean(), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("provider IN ('openai', 'gemini')", name="ck_platform_ai_provider"),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["platform_users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("provider"),
    )

    op.create_table(
        "platform_ai_task_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("attempts_per_route", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(_TASK_CHECK, name="ck_platform_ai_task_profiles_task"),
        sa.CheckConstraint(
            "timeout_seconds >= 1 AND timeout_seconds <= 120",
            name="ck_platform_ai_task_profiles_timeout",
        ),
        sa.CheckConstraint(
            "attempts_per_route >= 1 AND attempts_per_route <= 3",
            name="ck_platform_ai_task_profiles_attempts",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["platform_users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_type", name="uq_platform_ai_task_profiles_task"),
    )

    op.create_table(
        "platform_ai_task_routes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
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
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "provider IN ('openai', 'gemini')", name="ck_platform_ai_route_provider"
        ),
        sa.CheckConstraint(
            "priority >= 0 AND priority <= 9", name="ck_platform_ai_route_priority"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["platform_ai_task_profiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "priority", name="uq_platform_ai_route_priority"),
    )
    op.create_index(
        "ix_platform_ai_routes_profile",
        "platform_ai_task_routes",
        ["profile_id", "priority"],
    )

    op.create_table(
        "platform_billing_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("display_unit", sa.String(length=16), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "display_unit IN ('rial', 'toman')",
            name="ck_platform_billing_display_unit",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["platform_users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO platform_billing_settings (id, display_unit) "
            "VALUES (1, 'rial')"
        )
    )

    op.create_table(
        "platform_ai_model_pricing",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("input_per_million_rial", sa.BigInteger(), nullable=False),
        sa.Column("output_per_million_rial", sa.BigInteger(), nullable=False),
        sa.Column("audio_per_minute_rial", sa.BigInteger(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "provider IN ('openai', 'gemini')", name="ck_platform_price_provider"
        ),
        sa.CheckConstraint(
            "input_per_million_rial >= 0",
            name="ck_platform_price_input_nonnegative",
        ),
        sa.CheckConstraint(
            "output_per_million_rial >= 0",
            name="ck_platform_price_output_nonnegative",
        ),
        sa.CheckConstraint(
            "audio_per_minute_rial >= 0",
            name="ck_platform_price_audio_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["platform_users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "model_id",
            "effective_from",
            name="uq_platform_price_provider_model_effective",
        ),
    )
    op.create_index(
        "ix_platform_price_provider_model_effective",
        "platform_ai_model_pricing",
        ["provider", "model_id", "effective_from"],
    )

    op.create_table(
        "business_wallets",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "balance_rial", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO business_wallets (tenant_id, balance_rial) "
            "SELECT id, 0 FROM tenants"
        )
    )

    op.add_column(
        "ai_execution_traces",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_ai_execution_traces_message_id",
        "ai_execution_traces",
        "messages",
        ["message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_ai_execution_traces_tenant_message",
        "ai_execution_traces",
        ["tenant_id", "message_id"],
    )

    op.create_table(
        "business_wallet_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("amount_rial", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_rial", sa.BigInteger(), nullable=False),
        sa.Column("ai_execution_trace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model_id", sa.String(length=255), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("total_tokens", sa.BigInteger(), nullable=True),
        sa.Column("audio_seconds", sa.Float(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('credit', 'ai_debit')",
            name="ck_business_wallet_transaction_kind",
        ),
        sa.CheckConstraint(
            "amount_rial >= 0", name="ck_business_wallet_transaction_amount"
        ),
        sa.ForeignKeyConstraint(
            ["ai_execution_trace_id"], ["ai_execution_traces.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["platform_users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ai_execution_trace_id", name="uq_business_wallet_transaction_trace"
        ),
    )
    op.create_index(
        "ix_business_wallet_transactions_tenant_created",
        "business_wallet_transactions",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_business_wallet_transactions_message",
        "business_wallet_transactions",
        ["tenant_id", "message_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_business_wallet_transactions_message",
        table_name="business_wallet_transactions",
    )
    op.drop_index(
        "ix_business_wallet_transactions_tenant_created",
        table_name="business_wallet_transactions",
    )
    op.drop_table("business_wallet_transactions")
    op.drop_index(
        "ix_ai_execution_traces_tenant_message", table_name="ai_execution_traces"
    )
    op.drop_constraint(
        "fk_ai_execution_traces_message_id",
        "ai_execution_traces",
        type_="foreignkey",
    )
    op.drop_column("ai_execution_traces", "message_id")
    op.drop_table("business_wallets")
    op.drop_index(
        "ix_platform_price_provider_model_effective",
        table_name="platform_ai_model_pricing",
    )
    op.drop_table("platform_ai_model_pricing")
    op.drop_table("platform_billing_settings")
    op.drop_index("ix_platform_ai_routes_profile", table_name="platform_ai_task_routes")
    op.drop_table("platform_ai_task_routes")
    op.drop_table("platform_ai_task_profiles")
    op.drop_table("platform_ai_provider_credentials")
    op.drop_column("platform_users", "is_platform_owner")
