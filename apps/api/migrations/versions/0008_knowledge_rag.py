"""Knowledge bases, sources, chunks, permissions, and retrieval traces.

Revision ID: 0008_knowledge_rag
Revises: 0007_tool_runtime
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision: str = "0008_knowledge_rag"
down_revision: str | None = "0007_tool_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_bases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_knowledge_bases_id_tenant"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_knowledge_bases_tenant_name"),
    )
    op.create_index(
        "ix_knowledge_bases_tenant_active",
        "knowledge_bases",
        ["tenant_id", "is_active", "created_at"],
    )

    op.create_table(
        "knowledge_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=120), nullable=False),
        sa.Column("object_key", sa.String(length=700), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("embedding_provider", sa.String(length=50), nullable=True),
        sa.Column("embedding_model_id", sa.String(length=255), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'ready', 'failed')",
            name="ck_knowledge_sources_status",
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_knowledge_sources_size"),
        sa.CheckConstraint("chunk_count >= 0", name="ck_knowledge_sources_chunk_count"),
        sa.CheckConstraint(
            "embedding_dimension IS NULL OR embedding_dimension > 0",
            name="ck_knowledge_sources_embedding_dimension",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id", "tenant_id"],
            ["knowledge_bases.id", "knowledge_bases.tenant_id"],
            ondelete="CASCADE",
            name="fk_knowledge_sources_base_tenant",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["platform_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "knowledge_base_id",
            name="uq_knowledge_sources_id_tenant_base",
        ),
        sa.UniqueConstraint(
            "knowledge_base_id",
            "sha256",
            name="uq_knowledge_sources_base_sha256",
        ),
    )
    op.create_index(
        "ix_knowledge_sources_tenant_status",
        "knowledge_sources",
        ["tenant_id", "status", "updated_at"],
    )
    op.create_index(
        "ix_knowledge_sources_base_created",
        "knowledge_sources",
        ["knowledge_base_id", "created_at"],
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("embedding", VECTOR(), nullable=False),
        sa.Column("embedding_provider", sa.String(length=50), nullable=False),
        sa.Column("embedding_model_id", sa.String(length=255), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("ordinal >= 1", name="ck_knowledge_chunks_ordinal"),
        sa.CheckConstraint(
            "embedding_dimension > 0", name="ck_knowledge_chunks_embedding_dimension"
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "tenant_id", "knowledge_base_id"],
            [
                "knowledge_sources.id",
                "knowledge_sources.tenant_id",
                "knowledge_sources.knowledge_base_id",
            ],
            ondelete="CASCADE",
            name="fk_knowledge_chunks_source_tenant_base",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "ordinal", name="uq_knowledge_chunks_source_ordinal"),
    )
    op.create_index(
        "ix_knowledge_chunks_tenant_base",
        "knowledge_chunks",
        ["tenant_id", "knowledge_base_id"],
    )
    op.create_index(
        "ix_knowledge_chunks_embedding_signature",
        "knowledge_chunks",
        ["tenant_id", "embedding_provider", "embedding_model_id", "embedding_dimension"],
    )

    op.create_table(
        "agent_knowledge_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["agent_id", "tenant_id"],
            ["agents.id", "agents.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_knowledge_permissions_agent_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id", "tenant_id"],
            ["knowledge_bases.id", "knowledge_bases.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_knowledge_permissions_base_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_id",
            "knowledge_base_id",
            name="uq_agent_knowledge_permissions_agent_base",
        ),
    )
    op.create_index(
        "ix_agent_knowledge_permissions_tenant_agent",
        "agent_knowledge_permissions",
        ["tenant_id", "agent_id"],
    )
    op.create_index(
        "ix_agent_knowledge_permissions_tenant_base",
        "agent_knowledge_permissions",
        ["tenant_id", "knowledge_base_id"],
    )

    op.create_table(
        "knowledge_retrieval_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("query_sha256", sa.String(length=64), nullable=False),
        sa.Column("embedding_provider", sa.String(length=50), nullable=False),
        sa.Column("embedding_model_id", sa.String(length=255), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("knowledge_base_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("selected_chunks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("embedding_dimension > 0", name="ck_knowledge_retrieval_dimension"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_knowledge_retrieval_latency"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["agent_id", "tenant_id"],
            ["agents.id", "agents.tenant_id"],
            ondelete="RESTRICT",
            name="fk_knowledge_retrieval_agent_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_retrieval_tenant_created",
        "knowledge_retrieval_traces",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_knowledge_retrieval_agent_run",
        "knowledge_retrieval_traces",
        ["agent_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_retrieval_agent_run", table_name="knowledge_retrieval_traces")
    op.drop_index("ix_knowledge_retrieval_tenant_created", table_name="knowledge_retrieval_traces")
    op.drop_table("knowledge_retrieval_traces")
    op.drop_index(
        "ix_agent_knowledge_permissions_tenant_base", table_name="agent_knowledge_permissions"
    )
    op.drop_index(
        "ix_agent_knowledge_permissions_tenant_agent", table_name="agent_knowledge_permissions"
    )
    op.drop_table("agent_knowledge_permissions")
    op.drop_index("ix_knowledge_chunks_embedding_signature", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_tenant_base", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index("ix_knowledge_sources_base_created", table_name="knowledge_sources")
    op.drop_index("ix_knowledge_sources_tenant_status", table_name="knowledge_sources")
    op.drop_table("knowledge_sources")
    op.drop_index("ix_knowledge_bases_tenant_active", table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
