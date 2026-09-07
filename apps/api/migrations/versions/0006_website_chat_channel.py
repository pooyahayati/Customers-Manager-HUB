"""Website channel origins and public chat sessions.

Revision ID: 0006_website_chat_channel
Revises: 0005_agent_prompt_runtime
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_website_chat_channel"
down_revision: str | None = "0005_agent_prompt_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_channel_accounts_type", "channel_accounts", type_="check")
    op.create_check_constraint(
        "ck_channel_accounts_type",
        "channel_accounts",
        "channel_type IN ('telegram', 'website')",
    )

    op.create_table(
        "website_channel_origins",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("origin", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["channel_account_id", "tenant_id"],
            ["channel_accounts.id", "channel_accounts.tenant_id"],
            ondelete="CASCADE",
            name="fk_website_channel_origins_account_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_account_id",
            "origin",
            name="uq_website_channel_origins_account_origin",
        ),
    )
    op.create_index(
        "ix_website_channel_origins_tenant_account",
        "website_channel_origins",
        ["tenant_id", "channel_account_id"],
    )

    op.create_table(
        "website_chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("visitor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            ["channel_account_id", "tenant_id"],
            ["channel_accounts.id", "channel_accounts.tenant_id"],
            ondelete="CASCADE",
            name="fk_website_chat_sessions_account_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_account_id",
            "visitor_id",
            name="uq_website_chat_sessions_account_visitor",
        ),
        sa.UniqueConstraint("token_hash", name="uq_website_chat_sessions_token_hash"),
    )
    op.create_index(
        "ix_website_chat_sessions_tenant_account",
        "website_chat_sessions",
        ["tenant_id", "channel_account_id", "created_at"],
    )
    op.create_index(
        "ix_website_chat_sessions_expires",
        "website_chat_sessions",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_website_chat_sessions_expires", table_name="website_chat_sessions")
    op.drop_index(
        "ix_website_chat_sessions_tenant_account",
        table_name="website_chat_sessions",
    )
    op.drop_table("website_chat_sessions")
    op.drop_index(
        "ix_website_channel_origins_tenant_account",
        table_name="website_channel_origins",
    )
    op.drop_table("website_channel_origins")
    op.drop_constraint("ck_channel_accounts_type", "channel_accounts", type_="check")
    op.create_check_constraint(
        "ck_channel_accounts_type",
        "channel_accounts",
        "channel_type IN ('telegram')",
    )
