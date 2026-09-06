from datetime import UTC, datetime
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.agent_models import (
    Agent,
    AgentChannelAssignment,
    AgentPrompt,
    AgentPromptVersion,
    PromptVersionStatus,
)
from customers_manager_hub.channel_models import ChannelAccount
from customers_manager_hub.database import get_db_session
from customers_manager_hub.models import AuditEvent, TenantRole
from customers_manager_hub.tenants import TenantContextDependency, require_tenant_role

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/agents", tags=["agents"])
prompt_router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/prompts", tags=["prompts"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
ConfigWriteRole = frozenset({TenantRole.OWNER, TenantRole.ADMIN})
_PROMPT_CONTENT_LIMIT = 50_000


class PromptCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=_PROMPT_CONTENT_LIMIT)

    @field_validator("name", "content")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value must not be blank")
        return normalized


class PromptDraftPut(BaseModel):
    content: str = Field(min_length=1, max_length=_PROMPT_CONTENT_LIMIT)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Prompt content must not be blank")
        return normalized


class PromptVersionResponse(BaseModel):
    id: UUID
    version: int
    status: PromptVersionStatus
    content: str
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PromptResponse(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime
    versions: list[PromptVersionResponse]


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    prompt_id: UUID
    description: str | None = Field(default=None, max_length=1000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Agent name must not be blank")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    prompt_id: UUID | None = None
    description: str | None = Field(default=None, max_length=1000)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Agent name must not be blank")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if (
            self.name is None
            and self.prompt_id is None
            and self.description is None
            and self.is_active is None
        ):
            raise ValueError("At least one Agent setting must be supplied")
        return self


class AgentResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    prompt_id: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AgentAssignmentPut(BaseModel):
    agent_id: UUID


class AgentAssignmentResponse(BaseModel):
    id: UUID
    agent_id: UUID
    channel_account_id: UUID
    created_at: datetime
    updated_at: datetime


def prompt_version_response(version: AgentPromptVersion) -> PromptVersionResponse:
    return PromptVersionResponse(
        id=version.id,
        version=version.version,
        status=PromptVersionStatus(version.status),
        content=version.content,
        published_at=version.published_at,
        created_at=version.created_at,
        updated_at=version.updated_at,
    )


def prompt_response(prompt: AgentPrompt, versions: list[AgentPromptVersion]) -> PromptResponse:
    return PromptResponse(
        id=prompt.id,
        name=prompt.name,
        created_at=prompt.created_at,
        updated_at=prompt.updated_at,
        versions=[prompt_version_response(version) for version in versions],
    )


def agent_response(agent: Agent) -> AgentResponse:
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        prompt_id=agent.prompt_id,
        is_active=agent.is_active,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def assignment_response(assignment: AgentChannelAssignment) -> AgentAssignmentResponse:
    return AgentAssignmentResponse(
        id=assignment.id,
        agent_id=assignment.agent_id,
        channel_account_id=assignment.channel_account_id,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at,
    )


async def load_prompt(db: AsyncSession, tenant_id: UUID, prompt_id: UUID) -> AgentPrompt:
    prompt = await db.scalar(
        select(AgentPrompt).where(
            AgentPrompt.id == prompt_id,
            AgentPrompt.tenant_id == tenant_id,
        )
    )
    if prompt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")
    return prompt


async def lock_prompt(db: AsyncSession, tenant_id: UUID, prompt_id: UUID) -> AgentPrompt:
    prompt = await db.scalar(
        select(AgentPrompt)
        .where(
            AgentPrompt.id == prompt_id,
            AgentPrompt.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    if prompt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")
    return prompt


async def load_prompt_versions(
    db: AsyncSession,
    tenant_id: UUID,
    prompt_ids: list[UUID],
) -> dict[UUID, list[AgentPromptVersion]]:
    grouped: dict[UUID, list[AgentPromptVersion]] = {prompt_id: [] for prompt_id in prompt_ids}
    if not prompt_ids:
        return grouped
    versions = list(
        (
            await db.scalars(
                select(AgentPromptVersion)
                .where(
                    AgentPromptVersion.tenant_id == tenant_id,
                    AgentPromptVersion.prompt_id.in_(prompt_ids),
                )
                .order_by(AgentPromptVersion.prompt_id, AgentPromptVersion.version)
            )
        ).all()
    )
    for version in versions:
        grouped[version.prompt_id].append(version)
    return grouped


async def load_agent(db: AsyncSession, tenant_id: UUID, agent_id: UUID) -> Agent:
    agent = await db.scalar(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant_id,
        )
    )
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent


async def load_channel_account(db: AsyncSession, tenant_id: UUID, channel_account_id: UUID) -> ChannelAccount:
    account = await db.scalar(
        select(ChannelAccount).where(
            ChannelAccount.id == channel_account_id,
            ChannelAccount.tenant_id == tenant_id,
        )
    )
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    return account


@prompt_router.post("", response_model=PromptResponse)
async def create_prompt(
    payload: PromptCreate,
    response: Response,
    context: TenantContextDependency,
    db: DbSession,
) -> PromptResponse:
    require_tenant_role(context, ConfigWriteRole)
    prompt = AgentPrompt(tenant_id=context.tenant.id, name=payload.name)
    db.add(prompt)
    await db.flush()
    version = AgentPromptVersion(
        tenant_id=context.tenant.id,
        prompt_id=prompt.id,
        version=1,
        status=PromptVersionStatus.DRAFT.value,
        content=payload.content,
    )
    db.add(version)
    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="prompt.created",
            target_type="agent_prompt",
            target_id=prompt.id,
            details={"draft_version": 1},
        )
    )
    await db.commit()
    await db.refresh(prompt)
    await db.refresh(version)
    response.status_code = status.HTTP_201_CREATED
    return prompt_response(prompt, [version])


@prompt_router.get("", response_model=list[PromptResponse])
async def list_prompts(
    context: TenantContextDependency,
    db: DbSession,
) -> list[PromptResponse]:
    prompts = list(
        (
            await db.scalars(
                select(AgentPrompt)
                .where(AgentPrompt.tenant_id == context.tenant.id)
                .order_by(AgentPrompt.created_at, AgentPrompt.id)
            )
        ).all()
    )
    grouped = await load_prompt_versions(db, context.tenant.id, [prompt.id for prompt in prompts])
    return [prompt_response(prompt, grouped[prompt.id]) for prompt in prompts]


@prompt_router.get("/{prompt_id}", response_model=PromptResponse)
async def get_prompt(
    prompt_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> PromptResponse:
    prompt = await load_prompt(db, context.tenant.id, prompt_id)
    grouped = await load_prompt_versions(db, context.tenant.id, [prompt.id])
    return prompt_response(prompt, grouped[prompt.id])


@prompt_router.put("/{prompt_id}/draft", response_model=PromptResponse)
async def put_prompt_draft(
    prompt_id: UUID,
    payload: PromptDraftPut,
    context: TenantContextDependency,
    db: DbSession,
) -> PromptResponse:
    require_tenant_role(context, ConfigWriteRole)
    prompt = await lock_prompt(db, context.tenant.id, prompt_id)
    draft = await db.scalar(
        select(AgentPromptVersion).where(
            AgentPromptVersion.tenant_id == context.tenant.id,
            AgentPromptVersion.prompt_id == prompt.id,
            AgentPromptVersion.status == PromptVersionStatus.DRAFT.value,
        )
    )
    if draft is None:
        max_version = await db.scalar(
            select(func.max(AgentPromptVersion.version)).where(
                AgentPromptVersion.tenant_id == context.tenant.id,
                AgentPromptVersion.prompt_id == prompt.id,
            )
        )
        draft = AgentPromptVersion(
            tenant_id=context.tenant.id,
            prompt_id=prompt.id,
            version=(max_version or 0) + 1,
            status=PromptVersionStatus.DRAFT.value,
            content=payload.content,
        )
        db.add(draft)
    else:
        draft.content = payload.content

    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="prompt.draft_updated",
            target_type="agent_prompt",
            target_id=prompt.id,
            details={"draft_version": draft.version},
        )
    )
    await db.commit()
    await db.refresh(prompt)
    grouped = await load_prompt_versions(db, context.tenant.id, [prompt.id])
    return prompt_response(prompt, grouped[prompt.id])


@prompt_router.post("/{prompt_id}/publish", response_model=PromptResponse)
async def publish_prompt(
    prompt_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> PromptResponse:
    require_tenant_role(context, ConfigWriteRole)
    prompt = await lock_prompt(db, context.tenant.id, prompt_id)
    draft = await db.scalar(
        select(AgentPromptVersion).where(
            AgentPromptVersion.tenant_id == context.tenant.id,
            AgentPromptVersion.prompt_id == prompt.id,
            AgentPromptVersion.status == PromptVersionStatus.DRAFT.value,
        )
    )
    if draft is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Prompt has no draft")
    published = await db.scalar(
        select(AgentPromptVersion).where(
            AgentPromptVersion.tenant_id == context.tenant.id,
            AgentPromptVersion.prompt_id == prompt.id,
            AgentPromptVersion.status == PromptVersionStatus.PUBLISHED.value,
        )
    )
    if published is not None:
        published.status = PromptVersionStatus.ARCHIVED.value
        await db.flush()
    draft.status = PromptVersionStatus.PUBLISHED.value
    draft.published_at = datetime.now(UTC)
    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="prompt.published",
            target_type="agent_prompt",
            target_id=prompt.id,
            details={"published_version": draft.version},
        )
    )
    await db.commit()
    await db.refresh(prompt)
    grouped = await load_prompt_versions(db, context.tenant.id, [prompt.id])
    return prompt_response(prompt, grouped[prompt.id])


@router.post("", response_model=AgentResponse)
async def create_agent(
    payload: AgentCreate,
    response: Response,
    context: TenantContextDependency,
    db: DbSession,
) -> AgentResponse:
    require_tenant_role(context, ConfigWriteRole)
    prompt = await load_prompt(db, context.tenant.id, payload.prompt_id)
    agent = Agent(
        tenant_id=context.tenant.id,
        prompt_id=prompt.id,
        name=payload.name,
        description=payload.description,
        is_active=True,
    )
    db.add(agent)
    await db.flush()
    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="agent.created",
            target_type="agent",
            target_id=agent.id,
            details={"prompt_id": str(prompt.id)},
        )
    )
    await db.commit()
    await db.refresh(agent)
    response.status_code = status.HTTP_201_CREATED
    return agent_response(agent)


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    context: TenantContextDependency,
    db: DbSession,
) -> list[AgentResponse]:
    agents = list(
        (
            await db.scalars(
                select(Agent)
                .where(Agent.tenant_id == context.tenant.id)
                .order_by(Agent.created_at, Agent.id)
            )
        ).all()
    )
    return [agent_response(agent) for agent in agents]


@router.get("/assignments/channels/{channel_account_id}", response_model=AgentAssignmentResponse)
async def get_channel_assignment(
    channel_account_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> AgentAssignmentResponse:
    await load_channel_account(db, context.tenant.id, channel_account_id)
    assignment = await db.scalar(
        select(AgentChannelAssignment).where(
            AgentChannelAssignment.tenant_id == context.tenant.id,
            AgentChannelAssignment.channel_account_id == channel_account_id,
        )
    )
    if assignment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent assignment not found")
    return assignment_response(assignment)


@router.put("/assignments/channels/{channel_account_id}", response_model=AgentAssignmentResponse)
async def put_channel_assignment(
    channel_account_id: UUID,
    payload: AgentAssignmentPut,
    context: TenantContextDependency,
    db: DbSession,
) -> AgentAssignmentResponse:
    require_tenant_role(context, ConfigWriteRole)
    await load_channel_account(db, context.tenant.id, channel_account_id)
    agent = await load_agent(db, context.tenant.id, payload.agent_id)
    assignment = await db.scalar(
        select(AgentChannelAssignment).where(
            AgentChannelAssignment.tenant_id == context.tenant.id,
            AgentChannelAssignment.channel_account_id == channel_account_id,
        )
    )
    if assignment is None:
        assignment = AgentChannelAssignment(
            tenant_id=context.tenant.id,
            agent_id=agent.id,
            channel_account_id=channel_account_id,
        )
        db.add(assignment)
        await db.flush()
    else:
        assignment.agent_id = agent.id
    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="agent.channel_assigned",
            target_type="agent_channel_assignment",
            target_id=assignment.id,
            details={
                "agent_id": str(agent.id),
                "channel_account_id": str(channel_account_id),
            },
        )
    )
    await db.commit()
    await db.refresh(assignment)
    return assignment_response(assignment)


@router.delete("/assignments/channels/{channel_account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel_assignment(
    channel_account_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> None:
    require_tenant_role(context, ConfigWriteRole)
    await load_channel_account(db, context.tenant.id, channel_account_id)
    assignment = await db.scalar(
        select(AgentChannelAssignment).where(
            AgentChannelAssignment.tenant_id == context.tenant.id,
            AgentChannelAssignment.channel_account_id == channel_account_id,
        )
    )
    if assignment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent assignment not found")
    assignment_id = assignment.id
    agent_id = assignment.agent_id
    await db.delete(assignment)
    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="agent.channel_unassigned",
            target_type="agent_channel_assignment",
            target_id=assignment_id,
            details={
                "agent_id": str(agent_id),
                "channel_account_id": str(channel_account_id),
            },
        )
    )
    await db.commit()


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> AgentResponse:
    agent = await load_agent(db, context.tenant.id, agent_id)
    return agent_response(agent)


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: UUID,
    payload: AgentUpdate,
    context: TenantContextDependency,
    db: DbSession,
) -> AgentResponse:
    require_tenant_role(context, ConfigWriteRole)
    agent = await load_agent(db, context.tenant.id, agent_id)
    changed_fields: list[str] = []
    if payload.name is not None and payload.name != agent.name:
        agent.name = payload.name
        changed_fields.append("name")
    if payload.prompt_id is not None and payload.prompt_id != agent.prompt_id:
        prompt = await load_prompt(db, context.tenant.id, payload.prompt_id)
        agent.prompt_id = prompt.id
        changed_fields.append("prompt_id")
    if payload.description is not None and payload.description != agent.description:
        agent.description = payload.description
        changed_fields.append("description")
    if payload.is_active is not None and payload.is_active != agent.is_active:
        agent.is_active = payload.is_active
        changed_fields.append("is_active")
    if changed_fields:
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="agent.updated",
                target_type="agent",
                target_id=agent.id,
                details={"changed_fields": changed_fields},
            )
        )
        await db.commit()
        await db.refresh(agent)
    return agent_response(agent)
