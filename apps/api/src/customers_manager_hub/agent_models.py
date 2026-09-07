from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

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
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from customers_manager_hub.models import Base


class PromptVersionStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class AgentRunStatus(StrEnum):
    PENDING = "pending"
    GENERATED = "generated"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AgentPrompt(Base):
    __tablename__ = "agent_prompts"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_agent_prompts_id_tenant"),
        Index("ix_agent_prompts_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AgentPromptVersion(Base):
    __tablename__ = "agent_prompt_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_agent_prompt_versions_version"),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_agent_prompt_versions_status",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_agent_prompt_versions_id_tenant"),
        UniqueConstraint("prompt_id", "version", name="uq_agent_prompt_versions_prompt_version"),
        ForeignKeyConstraint(
            ["prompt_id", "tenant_id"],
            ["agent_prompts.id", "agent_prompts.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_prompt_versions_prompt_tenant",
        ),
        Index(
            "uq_agent_prompt_versions_one_draft",
            "prompt_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
        Index(
            "uq_agent_prompt_versions_one_published",
            "prompt_id",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
        Index("ix_agent_prompt_versions_tenant_prompt", "tenant_id", "prompt_id", "version"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    prompt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_agents_id_tenant"),
        ForeignKeyConstraint(
            ["prompt_id", "tenant_id"],
            ["agent_prompts.id", "agent_prompts.tenant_id"],
            ondelete="RESTRICT",
            name="fk_agents_prompt_tenant",
        ),
        Index("ix_agents_tenant_created", "tenant_id", "created_at"),
        Index("ix_agents_tenant_prompt", "tenant_id", "prompt_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    prompt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AgentChannelAssignment(Base):
    __tablename__ = "agent_channel_assignments"
    __table_args__ = (
        UniqueConstraint(
            "channel_account_id",
            name="uq_agent_channel_assignments_channel_account",
        ),
        ForeignKeyConstraint(
            ["agent_id", "tenant_id"],
            ["agents.id", "agents.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_channel_assignments_agent_tenant",
        ),
        ForeignKeyConstraint(
            ["channel_account_id", "tenant_id"],
            ["channel_accounts.id", "channel_accounts.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_channel_assignments_channel_tenant",
        ),
        Index("ix_agent_channel_assignments_tenant_agent", "tenant_id", "agent_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    agent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    channel_account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'generated', 'paused', 'succeeded', 'failed')",
            name="ck_agent_runs_status",
        ),
        UniqueConstraint(
            "tenant_id",
            "inbound_message_id",
            name="uq_agent_runs_tenant_inbound_message",
        ),
        ForeignKeyConstraint(
            ["agent_id", "tenant_id"],
            ["agents.id", "agents.tenant_id"],
            ondelete="RESTRICT",
            name="fk_agent_runs_agent_tenant",
        ),
        ForeignKeyConstraint(
            ["prompt_version_id", "tenant_id"],
            ["agent_prompt_versions.id", "agent_prompt_versions.tenant_id"],
            ondelete="RESTRICT",
            name="fk_agent_runs_prompt_version_tenant",
        ),
        ForeignKeyConstraint(
            ["channel_account_id", "tenant_id"],
            ["channel_accounts.id", "channel_accounts.tenant_id"],
            ondelete="RESTRICT",
            name="fk_agent_runs_channel_tenant",
        ),
        Index("ix_agent_runs_tenant_status", "tenant_id", "status", "updated_at"),
        Index("ix_agent_runs_lease", "lease_until"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    prompt_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    channel_account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    inbound_message_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    outbound_message_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AgentRunStatus.PENDING.value
    )
    generated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
