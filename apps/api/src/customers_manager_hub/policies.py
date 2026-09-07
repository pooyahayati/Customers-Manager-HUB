from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.database import AsyncSessionFactory, get_db_session
from customers_manager_hub.handoff_models import HandoffPolicy
from customers_manager_hub.models import AuditEvent, TenantRole
from customers_manager_hub.policy_models import (
    OutsideBusinessHoursAction,
    PolicyApprovalMode,
    PolicyAutonomyMode,
    PolicyDecisionAction,
    PolicyDecisionTrace,
    PolicyDecisionType,
    PolicyMessageAction,
    PolicyToolEffect,
    TenantPolicy,
    ToolPolicyRule,
)
from customers_manager_hub.policy_runtime import (
    EffectiveTenantPolicy,
    load_effective_tenant_policy,
    normalize_business_hours,
    normalize_handoff_keywords,
    normalize_message_rules,
    normalize_policy_timezone,
)
from customers_manager_hub.tenants import TenantContextDependency, require_tenant_role
from customers_manager_hub.tool_models import ToolDefinition, ToolRiskLevel

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/policies", tags=["policies"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
PolicyWriteRoles = frozenset({TenantRole.OWNER, TenantRole.ADMIN})


class TenantPolicyPut(BaseModel):
    enabled: bool = True
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    business_hours: dict[str, object] = Field(default_factory=dict)
    outside_business_hours_action: OutsideBusinessHoursAction = OutsideBusinessHoursAction.ALLOW
    autonomy_mode: PolicyAutonomyMode = PolicyAutonomyMode.AUTONOMOUS
    message_rules: dict[str, object] = Field(default_factory=dict)
    handoff_keywords: list[str] = Field(default_factory=list, max_length=50)
    handoff_on_tool_approval: bool = True
    approval_min_risk: ToolRiskLevel = ToolRiskLevel.HIGH
    require_approval_for_writes: bool = False

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        return normalize_policy_timezone(value)

    @field_validator("business_hours")
    @classmethod
    def validate_business_hours(cls, value: dict[str, object]) -> dict[str, object]:
        return normalize_business_hours(value)

    @field_validator("message_rules")
    @classmethod
    def validate_message_rules(cls, value: dict[str, object]) -> dict[str, object]:
        return normalize_message_rules(value)

    @field_validator("handoff_keywords")
    @classmethod
    def validate_handoff_keywords(cls, value: list[str]) -> list[str]:
        return normalize_handoff_keywords(value)


class TenantPolicyResponse(BaseModel):
    enabled: bool
    revision: int
    timezone: str
    business_hours: dict[str, object]
    outside_business_hours_action: OutsideBusinessHoursAction
    autonomy_mode: PolicyAutonomyMode
    message_rules: dict[str, object]
    handoff_keywords: list[str]
    handoff_on_tool_approval: bool
    approval_min_risk: ToolRiskLevel
    require_approval_for_writes: bool
    updated_at: datetime | None


class ToolPolicyRulePut(BaseModel):
    effect: PolicyToolEffect = PolicyToolEffect.ALLOW
    approval_mode: PolicyApprovalMode = PolicyApprovalMode.INHERIT


class ToolPolicyRuleResponse(BaseModel):
    id: UUID
    tool_id: UUID
    effect: PolicyToolEffect
    approval_mode: PolicyApprovalMode
    created_at: datetime
    updated_at: datetime


class PolicyTraceResponse(BaseModel):
    id: UUID
    policy_revision: int
    decision_type: PolicyDecisionType
    action: PolicyDecisionAction
    reason_code: str
    conversation_id: UUID | None
    message_id: UUID | None
    agent_id: UUID | None
    tool_id: UUID | None
    safe_context: dict[str, object]
    created_at: datetime


def _policy_response(row: TenantPolicy) -> TenantPolicyResponse:
    return TenantPolicyResponse(
        enabled=row.enabled,
        revision=row.revision,
        timezone=row.timezone,
        business_hours=dict(row.business_hours),
        outside_business_hours_action=OutsideBusinessHoursAction(row.outside_business_hours_action),
        autonomy_mode=PolicyAutonomyMode(row.autonomy_mode),
        message_rules=dict(row.message_rules),
        handoff_keywords=list(row.handoff_keywords),
        handoff_on_tool_approval=row.handoff_on_tool_approval,
        approval_min_risk=ToolRiskLevel(row.approval_min_risk),
        require_approval_for_writes=row.require_approval_for_writes,
        updated_at=row.updated_at,
    )


def _effective_policy_response(policy: EffectiveTenantPolicy) -> TenantPolicyResponse:
    return TenantPolicyResponse(
        enabled=policy.enabled,
        revision=policy.revision,
        timezone=policy.timezone,
        business_hours={},
        outside_business_hours_action=policy.outside_business_hours_action,
        autonomy_mode=policy.autonomy_mode,
        message_rules={key.value: value.value for key, value in policy.message_rules.items()},
        handoff_keywords=list(policy.handoff_keywords),
        handoff_on_tool_approval=policy.handoff_on_tool_approval,
        approval_min_risk=policy.approval_min_risk,
        require_approval_for_writes=policy.require_approval_for_writes,
        updated_at=None,
    )


def _tool_rule_response(rule: ToolPolicyRule) -> ToolPolicyRuleResponse:
    return ToolPolicyRuleResponse(
        id=rule.id,
        tool_id=rule.tool_id,
        effect=PolicyToolEffect(rule.effect),
        approval_mode=PolicyApprovalMode(rule.approval_mode),
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


def _trace_response(trace: PolicyDecisionTrace) -> PolicyTraceResponse:
    return PolicyTraceResponse(
        id=trace.id,
        policy_revision=trace.policy_revision,
        decision_type=PolicyDecisionType(trace.decision_type),
        action=PolicyDecisionAction(trace.action),
        reason_code=trace.reason_code,
        conversation_id=trace.conversation_id,
        message_id=trace.message_id,
        agent_id=trace.agent_id,
        tool_id=trace.tool_id,
        safe_context=dict(trace.safe_context),
        created_at=trace.created_at,
    )


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


async def _ensure_policy_row(
    db: AsyncSession,
    session_factory: AsyncSessionFactory,
    tenant_id: UUID,
) -> TenantPolicy:
    row = await db.scalar(select(TenantPolicy).where(TenantPolicy.tenant_id == tenant_id))
    if row is not None:
        return row
    effective = await load_effective_tenant_policy(session_factory, tenant_id)
    row = TenantPolicy(
        tenant_id=tenant_id,
        enabled=effective.enabled,
        revision=1,
        timezone=effective.timezone,
        business_hours={},
        outside_business_hours_action=effective.outside_business_hours_action.value,
        autonomy_mode=effective.autonomy_mode.value,
        message_rules={key.value: value.value for key, value in effective.message_rules.items()},
        handoff_keywords=list(effective.handoff_keywords),
        handoff_on_tool_approval=effective.handoff_on_tool_approval,
        approval_min_risk=effective.approval_min_risk.value,
        require_approval_for_writes=effective.require_approval_for_writes,
    )
    db.add(row)
    await db.flush()
    return row


def _session_factory_from_db(db: AsyncSession) -> AsyncSessionFactory:
    bind = db.bind
    if bind is None:
        raise RuntimeError("Database session has no bind")
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return cast(AsyncSessionFactory, async_sessionmaker(bind, expire_on_commit=False))


@router.get("", response_model=TenantPolicyResponse)
async def get_policy(
    context: TenantContextDependency,
    db: DbSession,
) -> TenantPolicyResponse:
    row = await db.scalar(select(TenantPolicy).where(TenantPolicy.tenant_id == context.tenant.id))
    if row is not None:
        return _policy_response(row)
    effective = await load_effective_tenant_policy(
        _session_factory_from_db(db),
        context.tenant.id,
    )
    return _effective_policy_response(effective)


@router.put("", response_model=TenantPolicyResponse)
async def put_policy(
    payload: TenantPolicyPut,
    context: TenantContextDependency,
    db: DbSession,
) -> TenantPolicyResponse:
    require_tenant_role(context, PolicyWriteRoles)
    row = await db.scalar(select(TenantPolicy).where(TenantPolicy.tenant_id == context.tenant.id))
    if row is None:
        row = TenantPolicy(
            tenant_id=context.tenant.id,
            enabled=payload.enabled,
            revision=1,
            timezone=payload.timezone,
            business_hours=payload.business_hours,
            outside_business_hours_action=payload.outside_business_hours_action.value,
            autonomy_mode=payload.autonomy_mode.value,
            message_rules=payload.message_rules,
            handoff_keywords=payload.handoff_keywords,
            handoff_on_tool_approval=payload.handoff_on_tool_approval,
            approval_min_risk=payload.approval_min_risk.value,
            require_approval_for_writes=payload.require_approval_for_writes,
        )
        db.add(row)
    else:
        row.enabled = payload.enabled
        row.revision += 1
        row.timezone = payload.timezone
        row.business_hours = payload.business_hours
        row.outside_business_hours_action = payload.outside_business_hours_action.value
        row.autonomy_mode = payload.autonomy_mode.value
        row.message_rules = payload.message_rules
        row.handoff_keywords = payload.handoff_keywords
        row.handoff_on_tool_approval = payload.handoff_on_tool_approval
        row.approval_min_risk = payload.approval_min_risk.value
        row.require_approval_for_writes = payload.require_approval_for_writes

    legacy = await db.scalar(
        select(HandoffPolicy).where(HandoffPolicy.tenant_id == context.tenant.id)
    )
    if legacy is None:
        legacy = HandoffPolicy(
            tenant_id=context.tenant.id,
            enabled=payload.enabled,
            customer_keywords=payload.handoff_keywords,
            pause_on_tool_approval=payload.handoff_on_tool_approval,
        )
        db.add(legacy)
    else:
        legacy.enabled = payload.enabled
        legacy.customer_keywords = payload.handoff_keywords
        legacy.pause_on_tool_approval = payload.handoff_on_tool_approval

    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="policy.updated",
            target_type="tenant_policy",
            target_id=None,
            details={
                "revision": row.revision,
                "enabled": row.enabled,
                "timezone": row.timezone,
                "outside_business_hours_action": row.outside_business_hours_action,
                "autonomy_mode": row.autonomy_mode,
                "message_rule_count": len(row.message_rules),
                "handoff_keyword_count": len(row.handoff_keywords),
                "approval_min_risk": row.approval_min_risk,
                "require_approval_for_writes": row.require_approval_for_writes,
            },
        )
    )
    await db.commit()
    await db.refresh(row)
    return _policy_response(row)


@router.get("/tool-rules", response_model=list[ToolPolicyRuleResponse])
async def list_tool_policy_rules(
    context: TenantContextDependency,
    db: DbSession,
) -> list[ToolPolicyRuleResponse]:
    rules = list(
        (
            await db.scalars(
                select(ToolPolicyRule)
                .where(ToolPolicyRule.tenant_id == context.tenant.id)
                .order_by(ToolPolicyRule.created_at, ToolPolicyRule.id)
            )
        ).all()
    )
    return [_tool_rule_response(rule) for rule in rules]


@router.put("/tool-rules/{tool_id}", response_model=ToolPolicyRuleResponse)
async def put_tool_policy_rule(
    tool_id: UUID,
    payload: ToolPolicyRulePut,
    context: TenantContextDependency,
    db: DbSession,
) -> ToolPolicyRuleResponse:
    require_tenant_role(context, PolicyWriteRoles)
    await _load_tool(db, context.tenant.id, tool_id)
    rule = await db.scalar(
        select(ToolPolicyRule).where(
            ToolPolicyRule.tenant_id == context.tenant.id,
            ToolPolicyRule.tool_id == tool_id,
        )
    )
    if rule is None:
        rule = ToolPolicyRule(
            tenant_id=context.tenant.id,
            tool_id=tool_id,
            effect=payload.effect.value,
            approval_mode=payload.approval_mode.value,
        )
        db.add(rule)
    else:
        rule.effect = payload.effect.value
        rule.approval_mode = payload.approval_mode.value
    policy = await _ensure_policy_row(db, _session_factory_from_db(db), context.tenant.id)
    policy.revision += 1
    await db.flush()
    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="policy.tool_rule.updated",
            target_type="tool_policy_rule",
            target_id=rule.id,
            details={
                "tool_id": str(tool_id),
                "effect": rule.effect,
                "approval_mode": rule.approval_mode,
                "policy_revision": policy.revision,
            },
        )
    )
    await db.commit()
    await db.refresh(rule)
    return _tool_rule_response(rule)


@router.delete("/tool-rules/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool_policy_rule(
    tool_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> Response:
    require_tenant_role(context, PolicyWriteRoles)
    rule = await db.scalar(
        select(ToolPolicyRule).where(
            ToolPolicyRule.tenant_id == context.tenant.id,
            ToolPolicyRule.tool_id == tool_id,
        )
    )
    if rule is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    policy = await _ensure_policy_row(db, _session_factory_from_db(db), context.tenant.id)
    policy.revision += 1
    rule_id = rule.id
    await db.delete(rule)
    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="policy.tool_rule.deleted",
            target_type="tool_policy_rule",
            target_id=rule_id,
            details={"tool_id": str(tool_id), "policy_revision": policy.revision},
        )
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/traces", response_model=list[PolicyTraceResponse])
async def list_policy_traces(
    context: TenantContextDependency,
    db: DbSession,
    decision_type: Annotated[PolicyDecisionType | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[PolicyTraceResponse]:
    statement = select(PolicyDecisionTrace).where(
        PolicyDecisionTrace.tenant_id == context.tenant.id
    )
    if decision_type is not None:
        statement = statement.where(PolicyDecisionTrace.decision_type == decision_type.value)
    traces = list(
        (
            await db.scalars(
                statement.order_by(
                    PolicyDecisionTrace.created_at.desc(),
                    PolicyDecisionTrace.id.desc(),
                ).limit(limit)
            )
        ).all()
    )
    return [_trace_response(trace) for trace in traces]
