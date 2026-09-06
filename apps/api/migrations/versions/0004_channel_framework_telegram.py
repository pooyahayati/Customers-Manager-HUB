"""Channel accounts, credentials, bindings, and inbound events.

Revision ID: 0004_channel_framework_telegram
Revises: 0003_ai_gateway_task_routing
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_channel_framework_telegram"
down_revision: str | None = "0003_ai_gateway_task_routing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "channel_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("external_account_id", sa.String(length=255), nullable=False),
        sa.Column("external_username", sa.String(length=255), nullable=True),
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
        sa.CheckConstraint("channel_type IN ('telegram')", name="ck_channel_accounts_type"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_channel_accounts_id_tenant"),
        sa.UniqueConstraint(
            "channel_type",
            "external_account_id",
            name="uq_channel_accounts_type_external",
        ),
    )
    op.create_index(
        "ix_channel_accounts_tenant_created",
        "channel_accounts",
        ["tenant_id", "created_at"],
    )

    op.create_table(
        "channel_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=False),
        sa.Column("key_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
            "kind IN ('telegram_bot_token', 'telegram_webhook_secret')",
            name="ck_channel_credentials_kind",
        ),
        sa.ForeignKeyConstraint(
            ["channel_account_id", "tenant_id"],
            ["channel_accounts.id", "channel_accounts.tenant_id"],
            ondelete="CASCADE",
            name="fk_channel_credentials_account_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_account_id",
            "kind",
            name="uq_channel_credentials_account_kind",
        ),
    )
    op.create_index(
        "ix_channel_credentials_tenant_account",
        "channel_credentials",
        ["tenant_id", "channel_account_id"],
    )

    op.create_table(
        "channel_account_capabilities",
        sa.Column("channel_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
            "capability IN ('text', 'voice')",
            name="ck_channel_account_capabilities_value",
        ),
        sa.ForeignKeyConstraint(
            ["channel_account_id", "tenant_id"],
            ["channel_accounts.id", "channel_accounts.tenant_id"],
            ondelete="CASCADE",
            name="fk_channel_account_capabilities_account_tenant",
        ),
        sa.PrimaryKeyConstraint("channel_account_id", "capability"),
    )
    op.create_index(
        "ix_channel_account_capabilities_tenant_account",
        "channel_account_capabilities",
        ["tenant_id", "channel_account_id"],
    )

    op.create_table(
        "conversation_channel_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_thread_id", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["channel_account_id", "tenant_id"],
            ["channel_accounts.id", "channel_accounts.tenant_id"],
            ondelete="CASCADE",
            name="fk_conversation_channel_bindings_account_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_account_id",
            "external_thread_id",
            name="uq_conversation_channel_bindings_account_thread",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            name="uq_conversation_channel_bindings_conversation",
        ),
    )
    op.create_index(
        "ix_conversation_channel_bindings_tenant_account",
        "conversation_channel_bindings",
        ["tenant_id", "channel_account_id"],
    )

    op.create_table(
        "channel_inbound_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
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
            "status IN ('received', 'enqueued', 'processed', 'ignored')",
            name="ck_channel_inbound_events_status",
        ),
        sa.ForeignKeyConstraint(
            ["channel_account_id", "tenant_id"],
            ["channel_accounts.id", "channel_accounts.tenant_id"],
            ondelete="CASCADE",
            name="fk_channel_inbound_events_account_tenant",
        ),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_account_id",
            "external_event_id",
            name="uq_channel_inbound_events_account_external",
        ),
    )
    op.create_index(
        "ix_channel_inbound_events_tenant_created",
        "channel_inbound_events",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_channel_inbound_events_status_updated",
        "channel_inbound_events",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_channel_inbound_events_status_updated", table_name="channel_inbound_events")
    op.drop_index("ix_channel_inbound_events_tenant_created", table_name="channel_inbound_events")
    op.drop_table("channel_inbound_events")

    op.drop_index(
        "ix_conversation_channel_bindings_tenant_account",
        table_name="conversation_channel_bindings",
    )
    op.drop_table("conversation_channel_bindings")

    op.drop_index(
        "ix_channel_account_capabilities_tenant_account",
        table_name="channel_account_capabilities",
    )
    op.drop_table("channel_account_capabilities")

    op.drop_index("ix_channel_credentials_tenant_account", table_name="channel_credentials")
    op.drop_table("channel_credentials")

    op.drop_index("ix_channel_accounts_tenant_created", table_name="channel_accounts")
    op.drop_table("channel_accounts")
