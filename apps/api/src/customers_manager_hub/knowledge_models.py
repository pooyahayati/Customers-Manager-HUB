from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from customers_manager_hub.models import Base


class KnowledgeSourceStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_knowledge_bases_id_tenant"),
        UniqueConstraint("tenant_id", "name", name="uq_knowledge_bases_tenant_name"),
        Index("ix_knowledge_bases_tenant_active", "tenant_id", "is_active", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class KnowledgeSource(Base):
    __tablename__ = "knowledge_sources"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processing', 'ready', 'failed')",
            name="ck_knowledge_sources_status",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_knowledge_sources_size"),
        CheckConstraint("chunk_count >= 0", name="ck_knowledge_sources_chunk_count"),
        CheckConstraint(
            "embedding_dimension IS NULL OR embedding_dimension > 0",
            name="ck_knowledge_sources_embedding_dimension",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "knowledge_base_id",
            name="uq_knowledge_sources_id_tenant_base",
        ),
        UniqueConstraint(
            "knowledge_base_id",
            "sha256",
            name="uq_knowledge_sources_base_sha256",
        ),
        ForeignKeyConstraint(
            ["knowledge_base_id", "tenant_id"],
            ["knowledge_bases.id", "knowledge_bases.tenant_id"],
            ondelete="CASCADE",
            name="fk_knowledge_sources_base_tenant",
        ),
        Index("ix_knowledge_sources_tenant_status", "tenant_id", "status", "updated_at"),
        Index("ix_knowledge_sources_base_created", "knowledge_base_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    object_key: Mapped[str] = mapped_column(String(700), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=KnowledgeSourceStatus.QUEUED.value
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    embedding_model_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        CheckConstraint("ordinal >= 1", name="ck_knowledge_chunks_ordinal"),
        CheckConstraint("embedding_dimension > 0", name="ck_knowledge_chunks_embedding_dimension"),
        UniqueConstraint("source_id", "ordinal", name="uq_knowledge_chunks_source_ordinal"),
        ForeignKeyConstraint(
            ["source_id", "tenant_id", "knowledge_base_id"],
            [
                "knowledge_sources.id",
                "knowledge_sources.tenant_id",
                "knowledge_sources.knowledge_base_id",
            ],
            ondelete="CASCADE",
            name="fk_knowledge_chunks_source_tenant_base",
        ),
        Index("ix_knowledge_chunks_tenant_base", "tenant_id", "knowledge_base_id"),
        Index(
            "ix_knowledge_chunks_embedding_signature",
            "tenant_id",
            "embedding_provider",
            "embedding_model_id",
            "embedding_dimension",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    embedding: Mapped[list[float]] = mapped_column(VECTOR(), nullable=False)
    embedding_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    embedding_model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentKnowledgePermission(Base):
    __tablename__ = "agent_knowledge_permissions"
    __table_args__ = (
        UniqueConstraint(
            "agent_id",
            "knowledge_base_id",
            name="uq_agent_knowledge_permissions_agent_base",
        ),
        ForeignKeyConstraint(
            ["agent_id", "tenant_id"],
            ["agents.id", "agents.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_knowledge_permissions_agent_tenant",
        ),
        ForeignKeyConstraint(
            ["knowledge_base_id", "tenant_id"],
            ["knowledge_bases.id", "knowledge_bases.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_knowledge_permissions_base_tenant",
        ),
        Index("ix_agent_knowledge_permissions_tenant_agent", "tenant_id", "agent_id"),
        Index("ix_agent_knowledge_permissions_tenant_base", "tenant_id", "knowledge_base_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    agent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KnowledgeRetrievalTrace(Base):
    __tablename__ = "knowledge_retrieval_traces"
    __table_args__ = (
        CheckConstraint("embedding_dimension > 0", name="ck_knowledge_retrieval_dimension"),
        CheckConstraint("latency_ms >= 0", name="ck_knowledge_retrieval_latency"),
        ForeignKeyConstraint(
            ["agent_id", "tenant_id"],
            ["agents.id", "agents.tenant_id"],
            ondelete="RESTRICT",
            name="fk_knowledge_retrieval_agent_tenant",
        ),
        Index("ix_knowledge_retrieval_tenant_created", "tenant_id", "created_at"),
        Index("ix_knowledge_retrieval_agent_run", "agent_run_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    agent_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    query_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    embedding_model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_base_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    selected_chunks: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
