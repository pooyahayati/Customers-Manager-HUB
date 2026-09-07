from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.agent_models import Agent
from customers_manager_hub.config import Settings, get_settings
from customers_manager_hub.database import get_db_session
from customers_manager_hub.models import AuditEvent, TenantRole
from customers_manager_hub.tenants import TenantContextDependency, require_tenant_role
from customers_manager_hub.tool_models import (
    AgentToolPermission,
    ToolAdapterKind,
    ToolApprovalStatus,
    ToolAuthType,
    ToolCredential,
    ToolDefinition,
    ToolExecution,
    ToolExecutionStatus,
    ToolOperationType,
    ToolRiskLevel,
)
from customers_manager_hub.tool_runtime import (
    normalize_header_name,
    normalize_tool_configuration,
    normalize_tool_name,
    validate_tool_schema,
)
from customers_manager_hub.tool_security import encrypt_tool_secret

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}", tags=["tools"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]
ConfigWriteRole = frozenset({TenantRole.OWNER, TenantRole.ADMIN})


class ToolCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    version: int = Field(default=1, ge=1)
    description: str = Field(min_length=1, max_length=1000)
    adapter_kind: ToolAdapterKind
    operation_type: ToolOperationType
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    configuration: dict[str, object]
    timeout_seconds: int = Field(default=15, ge=1, le=60)
    max_attempts: int = Field(default=1, ge=1, le=3)
    requires_approval: bool = False

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_tool_name(value)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Tool description must not be blank")
        return normalized

    @field_validator("input_schema", "output_schema")
    @classmethod
    def validate_schema(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_tool_schema(value)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        self.configuration = normalize_tool_configuration(
            self.adapter_kind,
            self.operation_type,
            self.configuration,
        )
        if self.adapter_kind == ToolAdapterKind.GOOGLE_SHEETS:
            required = self.input_schema.get("required")
            properties = self.input_schema.get("properties")
            if not isinstance(required, list) or "range" not in required:
                raise ValueError("Google Sheets input schema must require range")
            if not isinstance(properties, dict) or "range" not in properties:
                raise ValueError("Google Sheets input schema must define range")
        return self


class ToolUpdate(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=1000)
    risk_level: ToolRiskLevel | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=60)
    max_attempts: int | None = Field(default=None, ge=1, le=3)
    requires_approval: bool | None = None
    is_active: bool | None = None

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Tool description must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one Tool setting must be supplied")
        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} must not be null")
        return self


class ToolCredentialPut(BaseModel):
    auth_type: ToolAuthType
    secret: str = Field(min_length=1, max_length=10_000)
    header_name: str | None = Field(default=None, max_length=100)

    @field_validator("secret")
    @classmethod
    def normalize_secret(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Tool credential must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_auth(self) -> Self:
        if self.auth_type == ToolAuthType.BEARER:
            if self.header_name is not None:
                raise ValueError("Bearer credentials must not define header_name")
        else:
            if self.header_name is None:
                raise ValueError("Header credentials require header_name")
            self.header_name = normalize_header_name(self.header_name)
        return self


class ToolCredentialMetadata(BaseModel):
    configured: bool
    auth_type: ToolAuthType | None = None
    header_name: str | None = None
    updated_at: datetime | None = None


class ToolResponse(BaseModel):
    id: UUID
    name: str
    version: int
    description: str
    adapter_kind: ToolAdapterKind
    operation_type: ToolOperationType
    risk_level: ToolRiskLevel
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    configuration: dict[str, object]
    timeout_seconds: int
    max_attempts: int
    requires_approval: bool
    is_active: bool
    credential: ToolCredentialMetadata
    created_at: datetime
    updated_at: datetime


class AgentToolPermissionResponse(BaseModel):
    id: UUID
    agent_id: UUID
    tool_id: UUID
    created_at: datetime


class ToolExecutionResponse(BaseModel):
    id: UUID
    tool_id: UUID
    agent_id: UUID
    agent_run_id: UUID | None
    call_ordinal: int | None
    status: ToolExecutionStatus
    approval_status: ToolApprovalStatus
    input_payload: dict[str, object]
    output_payload: dict[str, object] | None
    error_code: str | None
    attempt_count: int
    duration_ms: int | None
    approved_by_user_id: UUID | None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"


class ToolApprovalPut(BaseModel):
    decision: ApprovalDecision


async def _load_tool(db: AsyncSession, tenant_id: UUID, tool_id: UUID) -> ToolDefinition:
    tool = await db.scalar(
        select(ToolDefinition).where(
            ToolDefinition.id == tool_id,
            ToolDefinition.tenant_id == tenant_id,
        )
    )
    if tool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")
    return tool


async def _load_agent(db: AsyncSession, tenant_id: UUID, agent_id: UUID) -> Agent:
    agent = await db.scalar(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant_id,
        )
    )
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent


async def _load_credential_metadata(
    db: AsyncSession,
    tenant_id: UUID,
    tool_ids: list[UUID],
) -> dict[UUID, ToolCredentialMetadata]:
    metadata = {tool_id: ToolCredentialMetadata(configured=False) for tool_id in tool_ids}
    if not tool_ids:
        return metadata
    credentials = list(
        (
            await db.scalars(
                select(ToolCredential).where(
                    ToolCredential.tenant_id == tenant_id,
                    ToolCredential.tool_id.in_(tool_ids),
                )
            )
        ).all()
    )
    for credential in credentials:
        metadata[credential.tool_id] = ToolCredentialMetadata(
            configured=True,
            auth_type=ToolAuthType(credential.auth_type),
            header_name=credential.header_name,
            updated_at=credential.updated_at,
        )
    return metadata


def _tool_response(tool: ToolDefinition, credential: ToolCredentialMetadata) -> ToolResponse:
    return ToolResponse(
        id=tool.id,
        name=tool.name,
        version=tool.version,
        description=tool.description,
        adapter_kind=ToolAdapterKind(tool.adapter_kind),
        operation_type=ToolOperationType(tool.operation_type),
        risk_level=ToolRiskLevel(tool.risk_level),
        input_schema=dict(tool.input_schema),
        output_schema=dict(tool.output_schema),
        configuration=dict(tool.configuration),
        timeout_seconds=tool.timeout_seconds,
        max_attempts=tool.max_attempts,
        requires_approval=tool.requires_approval,
        is_active=tool.is_active,
        credential=credential,
        created_at=tool.created_at,
        updated_at=tool.updated_at,
    )


def _permission_response(permission: AgentToolPermission) -> AgentToolPermissionResponse:
    return AgentToolPermissionResponse(
        id=permission.id,
        agent_id=permission.agent_id,
        tool_id=permission.tool_id,
        created_at=permission.created_at,
    )


def _execution_response(execution: ToolExecution) -> ToolExecutionResponse:
    return ToolExecutionResponse(
        id=execution.id,
        tool_id=execution.tool_id,
        agent_id=execution.agent_id,
        agent_run_id=execution.agent_run_id,
        call_ordinal=execution.call_ordinal,
        status=ToolExecutionStatus(execution.status),
        approval_status=ToolApprovalStatus(execution.approval_status),
        input_payload=dict(execution.input_payload),
        output_payload=(
            dict(execution.output_payload) if execution.output_payload is not None else None
        ),
        error_code=execution.error_code,
        attempt_count=execution.attempt_count,
        duration_ms=execution.duration_ms,
        approved_by_user_id=execution.approved_by_user_id,
        approved_at=execution.approved_at,
        created_at=execution.created_at,
        updated_at=execution.updated_at,
    )


@router.post("/tools", response_model=ToolResponse, status_code=201)
async def create_tool(
    payload: ToolCreate,
    context: TenantContextDependency,
    db: DbSession,
) -> ToolResponse:
    require_tenant_role(context, ConfigWriteRole)
    tool = ToolDefinition(
        tenant_id=context.tenant.id,
        name=payload.name,
        version=payload.version,
        description=payload.description,
        adapter_kind=payload.adapter_kind.value,
        operation_type=payload.operation_type.value,
        risk_level=payload.risk_level.value,
        input_schema=payload.input_schema,
        output_schema=payload.output_schema,
        configuration=payload.configuration,
        timeout_seconds=payload.timeout_seconds,
        max_attempts=payload.max_attempts,
        requires_approval=payload.requires_approval,
        is_active=True,
    )
    db.add(tool)
    try:
        await db.flush()
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="tool.created",
                target_type="tool_definition",
                target_id=tool.id,
                details={
                    "name": tool.name,
                    "version": tool.version,
                    "adapter_kind": tool.adapter_kind,
                    "operation_type": tool.operation_type,
                    "risk_level": tool.risk_level,
                },
            )
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Tool already exists"
        ) from exc
    await db.refresh(tool)
    return _tool_response(tool, ToolCredentialMetadata(configured=False))


@router.get("/tools", response_model=list[ToolResponse])
async def list_tools(
    context: TenantContextDependency,
    db: DbSession,
) -> list[ToolResponse]:
    tools = list(
        (
            await db.scalars(
                select(ToolDefinition)
                .where(ToolDefinition.tenant_id == context.tenant.id)
                .order_by(ToolDefinition.name, ToolDefinition.version, ToolDefinition.id)
            )
        ).all()
    )
    credentials = await _load_credential_metadata(
        db, context.tenant.id, [tool.id for tool in tools]
    )
    return [_tool_response(tool, credentials[tool.id]) for tool in tools]


@router.get("/tools/{tool_id}", response_model=ToolResponse)
async def get_tool(
    tool_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> ToolResponse:
    tool = await _load_tool(db, context.tenant.id, tool_id)
    credentials = await _load_credential_metadata(db, context.tenant.id, [tool.id])
    return _tool_response(tool, credentials[tool.id])


@router.patch("/tools/{tool_id}", response_model=ToolResponse)
async def update_tool(
    tool_id: UUID,
    payload: ToolUpdate,
    context: TenantContextDependency,
    db: DbSession,
) -> ToolResponse:
    require_tenant_role(context, ConfigWriteRole)
    tool = await _load_tool(db, context.tenant.id, tool_id)
    changed: list[str] = []
    for field_name in payload.model_fields_set:
        value = getattr(payload, field_name)
        stored = value.value if isinstance(value, StrEnum) else value
        if getattr(tool, field_name) != stored:
            setattr(tool, field_name, stored)
            changed.append(field_name)
    if changed:
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="tool.updated",
                target_type="tool_definition",
                target_id=tool.id,
                details={"changed_fields": sorted(changed)},
            )
        )
        await db.commit()
        await db.refresh(tool)
    credentials = await _load_credential_metadata(db, context.tenant.id, [tool.id])
    return _tool_response(tool, credentials[tool.id])


@router.put("/tools/{tool_id}/credential", response_model=ToolCredentialMetadata)
async def put_tool_credential(
    tool_id: UUID,
    payload: ToolCredentialPut,
    context: TenantContextDependency,
    db: DbSession,
    settings: SettingsDependency,
) -> ToolCredentialMetadata:
    require_tenant_role(context, ConfigWriteRole)
    tool = await _load_tool(db, context.tenant.id, tool_id)
    encrypted = encrypt_tool_secret(
        settings,
        context.tenant.id,
        tool.id,
        payload.auth_type,
        payload.secret,
        header_name=payload.header_name,
    )
    credential = await db.scalar(
        select(ToolCredential).where(
            ToolCredential.tenant_id == context.tenant.id,
            ToolCredential.tool_id == tool.id,
        )
    )
    if credential is None:
        credential = ToolCredential(
            tenant_id=context.tenant.id,
            tool_id=tool.id,
            auth_type=payload.auth_type.value,
            header_name=payload.header_name,
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
            key_version=encrypted.key_version,
        )
        db.add(credential)
    else:
        credential.auth_type = payload.auth_type.value
        credential.header_name = payload.header_name
        credential.ciphertext = encrypted.ciphertext
        credential.nonce = encrypted.nonce
        credential.key_version = encrypted.key_version
    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="tool.credential.set",
            target_type="tool_definition",
            target_id=tool.id,
            details={
                "auth_type": payload.auth_type.value,
                "header_name": payload.header_name,
            },
        )
    )
    await db.commit()
    await db.refresh(credential)
    return ToolCredentialMetadata(
        configured=True,
        auth_type=payload.auth_type,
        header_name=payload.header_name,
        updated_at=credential.updated_at,
    )


@router.delete("/tools/{tool_id}/credential", status_code=204)
async def delete_tool_credential(
    tool_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> Response:
    require_tenant_role(context, ConfigWriteRole)
    tool = await _load_tool(db, context.tenant.id, tool_id)
    credential = await db.scalar(
        select(ToolCredential).where(
            ToolCredential.tenant_id == context.tenant.id,
            ToolCredential.tool_id == tool.id,
        )
    )
    if credential is not None:
        await db.delete(credential)
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="tool.credential.deleted",
                target_type="tool_definition",
                target_id=tool.id,
                details={},
            )
        )
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/agents/{agent_id}/tools",
    response_model=list[AgentToolPermissionResponse],
)
async def list_agent_tool_permissions(
    agent_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> list[AgentToolPermissionResponse]:
    await _load_agent(db, context.tenant.id, agent_id)
    permissions = list(
        (
            await db.scalars(
                select(AgentToolPermission)
                .where(
                    AgentToolPermission.tenant_id == context.tenant.id,
                    AgentToolPermission.agent_id == agent_id,
                )
                .order_by(AgentToolPermission.created_at, AgentToolPermission.id)
            )
        ).all()
    )
    return [_permission_response(item) for item in permissions]


@router.put(
    "/agents/{agent_id}/tools/{tool_id}",
    response_model=AgentToolPermissionResponse,
)
async def grant_agent_tool_permission(
    agent_id: UUID,
    tool_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> AgentToolPermissionResponse:
    require_tenant_role(context, ConfigWriteRole)
    await _load_agent(db, context.tenant.id, agent_id)
    await _load_tool(db, context.tenant.id, tool_id)
    permission = await db.scalar(
        select(AgentToolPermission).where(
            AgentToolPermission.tenant_id == context.tenant.id,
            AgentToolPermission.agent_id == agent_id,
            AgentToolPermission.tool_id == tool_id,
        )
    )
    if permission is None:
        permission = AgentToolPermission(
            tenant_id=context.tenant.id,
            agent_id=agent_id,
            tool_id=tool_id,
        )
        db.add(permission)
        await db.flush()
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="tool.permission.granted",
                target_type="agent_tool_permission",
                target_id=permission.id,
                details={"agent_id": str(agent_id), "tool_id": str(tool_id)},
            )
        )
        await db.commit()
        await db.refresh(permission)
    return _permission_response(permission)


@router.delete("/agents/{agent_id}/tools/{tool_id}", status_code=204)
async def revoke_agent_tool_permission(
    agent_id: UUID,
    tool_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> Response:
    require_tenant_role(context, ConfigWriteRole)
    await _load_agent(db, context.tenant.id, agent_id)
    await _load_tool(db, context.tenant.id, tool_id)
    permission = await db.scalar(
        select(AgentToolPermission).where(
            AgentToolPermission.tenant_id == context.tenant.id,
            AgentToolPermission.agent_id == agent_id,
            AgentToolPermission.tool_id == tool_id,
        )
    )
    if permission is not None:
        permission_id = permission.id
        await db.delete(permission)
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="tool.permission.revoked",
                target_type="agent_tool_permission",
                target_id=permission_id,
                details={"agent_id": str(agent_id), "tool_id": str(tool_id)},
            )
        )
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/tool-executions", response_model=list[ToolExecutionResponse])
async def list_tool_executions(
    context: TenantContextDependency,
    db: DbSession,
) -> list[ToolExecutionResponse]:
    executions = list(
        (
            await db.scalars(
                select(ToolExecution)
                .where(ToolExecution.tenant_id == context.tenant.id)
                .order_by(ToolExecution.created_at.desc(), ToolExecution.id.desc())
                .limit(100)
            )
        ).all()
    )
    return [_execution_response(item) for item in executions]


@router.get("/tool-executions/{execution_id}", response_model=ToolExecutionResponse)
async def get_tool_execution(
    execution_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> ToolExecutionResponse:
    execution = await db.scalar(
        select(ToolExecution).where(
            ToolExecution.id == execution_id,
            ToolExecution.tenant_id == context.tenant.id,
        )
    )
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool execution not found"
        )
    return _execution_response(execution)


@router.put(
    "/tool-executions/{execution_id}/approval",
    response_model=ToolExecutionResponse,
)
async def decide_tool_execution_approval(
    execution_id: UUID,
    payload: ToolApprovalPut,
    context: TenantContextDependency,
    db: DbSession,
) -> ToolExecutionResponse:
    require_tenant_role(context, ConfigWriteRole)
    execution = await db.scalar(
        select(ToolExecution)
        .where(
            ToolExecution.id == execution_id,
            ToolExecution.tenant_id == context.tenant.id,
        )
        .with_for_update()
    )
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool execution not found"
        )
    if execution.approval_status != ToolApprovalStatus.PENDING.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval is not pending")
    now = datetime.now(UTC)
    execution.approved_by_user_id = context.current.user.id
    execution.approved_at = now
    if payload.decision == ApprovalDecision.APPROVED:
        execution.approval_status = ToolApprovalStatus.APPROVED.value
        execution.status = ToolExecutionStatus.PENDING.value
    else:
        execution.approval_status = ToolApprovalStatus.DENIED.value
        execution.status = ToolExecutionStatus.DENIED.value
        execution.error_code = "tool_approval_denied"
    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action=f"tool.approval.{payload.decision.value}",
            target_type="tool_execution",
            target_id=execution.id,
            details={"tool_id": str(execution.tool_id), "agent_id": str(execution.agent_id)},
        )
    )
    await db.commit()
    await db.refresh(execution)
    return _execution_response(execution)
