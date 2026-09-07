from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.agent_models import AgentRun
from customers_manager_hub.ai_models import AIExecutionTrace
from customers_manager_hub.analytics_models import AIModelPricing
from customers_manager_hub.database import get_db_session
from customers_manager_hub.handoff_models import ConversationHandoff
from customers_manager_hub.models import (
    AuditEvent,
    Conversation,
    ConversationStatus,
    Message,
    MessageAuthorType,
    MessageDirection,
    TenantRole,
)
from customers_manager_hub.tenants import TenantContextDependency, require_tenant_role
from customers_manager_hub.tool_models import ToolDefinition, ToolExecution, ToolExecutionStatus

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/analytics", tags=["analytics"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
PricingWriteRoles = frozenset({TenantRole.OWNER, TenantRole.ADMIN})
_MAX_REPORT_WINDOW = timedelta(days=366)
_DEFAULT_REPORT_WINDOW = timedelta(days=30)
_MILLION = Decimal("1000000")
_MINUTE_SECONDS = Decimal("60")


class ReportWindow(BaseModel):
    start: datetime
    end: datetime


async def report_window(
    from_time: Annotated[datetime | None, Query(alias="from")] = None,
    to_time: Annotated[datetime | None, Query(alias="to")] = None,
) -> ReportWindow:
    end = _normalize_report_timestamp(to_time, "to") if to_time is not None else datetime.now(UTC)
    start = (
        _normalize_report_timestamp(from_time, "from")
        if from_time is not None
        else end - _DEFAULT_REPORT_WINDOW
    )
    if start >= end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Analytics 'from' must be before 'to'",
        )
    if end - start > _MAX_REPORT_WINDOW:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Analytics report window cannot exceed 366 days",
        )
    return ReportWindow(start=start, end=end)


AnalyticsWindow = Annotated[ReportWindow, Depends(report_window)]


class PricingPut(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    model_id: str = Field(min_length=1, max_length=255)
    input_per_million_usd: Decimal = Field(default=Decimal("0"), ge=0)
    output_per_million_usd: Decimal = Field(default=Decimal("0"), ge=0)
    audio_per_minute_usd: Decimal = Field(default=Decimal("0"), ge=0)
    effective_from: datetime

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("Provider must not be blank")
        return normalized

    @field_validator("model_id")
    @classmethod
    def normalize_model_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Model ID must not be blank")
        return normalized

    @field_validator("effective_from")
    @classmethod
    def normalize_effective_from(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Pricing effective_from must include a timezone")
        return value.astimezone(UTC)


class PricingResponse(BaseModel):
    id: UUID
    provider: str
    model_id: str
    input_per_million_usd: Decimal
    output_per_million_usd: Decimal
    audio_per_minute_usd: Decimal
    effective_from: datetime
    created_at: datetime
    updated_at: datetime


class AIUsageBreakdown(BaseModel):
    provider: str
    model_id: str
    task_type: str
    requests: int
    succeeded: int
    failed: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    audio_seconds: float
    average_latency_ms: float | None
    estimated_cost_usd: Decimal
    unpriced_requests: int


class AIUsageResponse(BaseModel):
    start: datetime
    end: datetime
    requests: int
    succeeded: int
    failed: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    audio_seconds: float
    estimated_cost_usd: Decimal
    unpriced_requests: int
    breakdown: list[AIUsageBreakdown]


class ToolMetric(BaseModel):
    tool_id: UUID
    tool_name: str
    executions: int
    succeeded: int
    failed: int
    denied: int
    approval_required: int
    average_duration_ms: float | None
    success_rate: float | None


class ToolMetricsResponse(BaseModel):
    start: datetime
    end: datetime
    executions: int
    succeeded: int
    failed: int
    denied: int
    approval_required: int
    success_rate: float | None
    tools: list[ToolMetric]


class OperationalOverview(BaseModel):
    conversations: int
    unique_contacts: int
    first_response_average_seconds: float | None
    first_response_samples: int
    resolution_average_seconds: float | None
    resolution_samples: int
    handoff_rate: float | None
    handoff_conversations: int
    handoff_average_seconds: float | None
    handoff_duration_samples: int
    ai_automation_rate: float | None
    automated_conversations: int
    active_conversations: int


class AnalyticsOverviewResponse(BaseModel):
    start: datetime
    end: datetime
    operations: OperationalOverview
    ai: AIUsageResponse
    tools: ToolMetricsResponse


@dataclass
class _UsageAccumulator:
    requests: int = 0
    succeeded: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    audio_seconds: float = 0.0
    latency_total_ms: int = 0
    latency_samples: int = 0
    estimated_cost_usd: Decimal = Decimal("0")
    unpriced_requests: int = 0


@dataclass
class _ToolAccumulator:
    executions: int = 0
    succeeded: int = 0
    failed: int = 0
    denied: int = 0
    approval_required: int = 0
    duration_total_ms: int = 0
    duration_samples: int = 0


def _normalize_report_timestamp(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Analytics '{field_name}' must include a timezone",
        )
    return value.astimezone(UTC)


def _pricing_response(row: AIModelPricing) -> PricingResponse:
    return PricingResponse(
        id=row.id,
        provider=row.provider,
        model_id=row.model_id,
        input_per_million_usd=row.input_per_million_usd,
        output_per_million_usd=row.output_per_million_usd,
        audio_per_minute_usd=row.audio_per_minute_usd,
        effective_from=row.effective_from,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _has_consumption(trace: AIExecutionTrace) -> bool:
    return bool(
        (trace.input_tokens or 0)
        or (trace.output_tokens or 0)
        or (trace.total_tokens or 0)
        or (trace.audio_seconds or 0)
    )


def _resolve_pricing(
    trace: AIExecutionTrace,
    pricing: dict[tuple[str, str], list[AIModelPricing]],
) -> AIModelPricing | None:
    candidates = pricing.get((trace.provider, trace.model_id), [])
    for row in reversed(candidates):
        if row.effective_from <= trace.created_at:
            return row
    return None


def _trace_cost(trace: AIExecutionTrace, price: AIModelPricing | None) -> Decimal | None:
    if not _has_consumption(trace):
        return Decimal("0")
    if price is None:
        return None
    if (
        (trace.total_tokens or 0) > 0
        and trace.input_tokens is None
        and trace.output_tokens is None
    ):
        return None
    input_cost = Decimal(trace.input_tokens or 0) / _MILLION * price.input_per_million_usd
    output_cost = Decimal(trace.output_tokens or 0) / _MILLION * price.output_per_million_usd
    audio_cost = (
        Decimal(str(trace.audio_seconds or 0.0))
        / _MINUTE_SECONDS
        * price.audio_per_minute_usd
    )
    return input_cost + output_cost + audio_cost


def _record_usage(
    accumulator: _UsageAccumulator,
    trace: AIExecutionTrace,
    cost: Decimal | None,
) -> None:
    accumulator.requests += 1
    if trace.status == "succeeded":
        accumulator.succeeded += 1
    else:
        accumulator.failed += 1
    accumulator.input_tokens += trace.input_tokens or 0
    accumulator.output_tokens += trace.output_tokens or 0
    accumulator.total_tokens += trace.total_tokens or 0
    accumulator.audio_seconds += trace.audio_seconds or 0.0
    accumulator.latency_total_ms += trace.latency_ms
    accumulator.latency_samples += 1
    if cost is None:
        accumulator.unpriced_requests += 1
    else:
        accumulator.estimated_cost_usd += cost


def _usage_breakdown(
    key: tuple[str, str, str], accumulator: _UsageAccumulator
) -> AIUsageBreakdown:
    provider, model_id, task_type = key
    average_latency = (
        accumulator.latency_total_ms / accumulator.latency_samples
        if accumulator.latency_samples
        else None
    )
    return AIUsageBreakdown(
        provider=provider,
        model_id=model_id,
        task_type=task_type,
        requests=accumulator.requests,
        succeeded=accumulator.succeeded,
        failed=accumulator.failed,
        input_tokens=accumulator.input_tokens,
        output_tokens=accumulator.output_tokens,
        total_tokens=accumulator.total_tokens,
        audio_seconds=round(accumulator.audio_seconds, 3),
        average_latency_ms=average_latency,
        estimated_cost_usd=accumulator.estimated_cost_usd,
        unpriced_requests=accumulator.unpriced_requests,
    )


async def _load_ai_usage(
    db: AsyncSession,
    tenant_id: UUID,
    window: ReportWindow,
) -> AIUsageResponse:
    traces = list(
        (
            await db.scalars(
                select(AIExecutionTrace)
                .where(
                    AIExecutionTrace.tenant_id == tenant_id,
                    AIExecutionTrace.created_at >= window.start,
                    AIExecutionTrace.created_at < window.end,
                )
                .order_by(AIExecutionTrace.created_at, AIExecutionTrace.id)
            )
        ).all()
    )
    price_rows = list(
        (
            await db.scalars(
                select(AIModelPricing)
                .where(
                    AIModelPricing.tenant_id == tenant_id,
                    AIModelPricing.effective_from < window.end,
                )
                .order_by(
                    AIModelPricing.provider,
                    AIModelPricing.model_id,
                    AIModelPricing.effective_from,
                    AIModelPricing.id,
                )
            )
        ).all()
    )
    pricing: dict[tuple[str, str], list[AIModelPricing]] = {}
    for row in price_rows:
        pricing.setdefault((row.provider, row.model_id), []).append(row)

    total = _UsageAccumulator()
    groups: dict[tuple[str, str, str], _UsageAccumulator] = {}
    for trace in traces:
        cost = _trace_cost(trace, _resolve_pricing(trace, pricing))
        _record_usage(total, trace, cost)
        key = (trace.provider, trace.model_id, trace.task_type)
        accumulator = groups.setdefault(key, _UsageAccumulator())
        _record_usage(accumulator, trace, cost)

    breakdown = [
        _usage_breakdown(key, groups[key])
        for key in sorted(groups, key=lambda value: (value[0], value[1], value[2]))
    ]
    return AIUsageResponse(
        start=window.start,
        end=window.end,
        requests=total.requests,
        succeeded=total.succeeded,
        failed=total.failed,
        input_tokens=total.input_tokens,
        output_tokens=total.output_tokens,
        total_tokens=total.total_tokens,
        audio_seconds=round(total.audio_seconds, 3),
        estimated_cost_usd=total.estimated_cost_usd,
        unpriced_requests=total.unpriced_requests,
        breakdown=breakdown,
    )


def _record_tool(accumulator: _ToolAccumulator, execution: ToolExecution) -> None:
    accumulator.executions += 1
    if execution.status == ToolExecutionStatus.SUCCEEDED.value:
        accumulator.succeeded += 1
    elif execution.status == ToolExecutionStatus.FAILED.value:
        accumulator.failed += 1
    elif execution.status == ToolExecutionStatus.DENIED.value:
        accumulator.denied += 1
    elif execution.status == ToolExecutionStatus.APPROVAL_REQUIRED.value:
        accumulator.approval_required += 1
    if execution.duration_ms is not None:
        accumulator.duration_total_ms += execution.duration_ms
        accumulator.duration_samples += 1


def _tool_success_rate(accumulator: _ToolAccumulator) -> float | None:
    terminal = accumulator.succeeded + accumulator.failed
    return accumulator.succeeded / terminal if terminal else None


async def _load_tool_metrics(
    db: AsyncSession,
    tenant_id: UUID,
    window: ReportWindow,
) -> ToolMetricsResponse:
    executions = list(
        (
            await db.scalars(
                select(ToolExecution)
                .where(
                    ToolExecution.tenant_id == tenant_id,
                    ToolExecution.created_at >= window.start,
                    ToolExecution.created_at < window.end,
                )
                .order_by(ToolExecution.created_at, ToolExecution.id)
            )
        ).all()
    )
    tool_ids = {execution.tool_id for execution in executions}
    tool_names: dict[UUID, str] = {}
    if tool_ids:
        definitions = list(
            (
                await db.scalars(
                    select(ToolDefinition).where(
                        ToolDefinition.tenant_id == tenant_id,
                        ToolDefinition.id.in_(tool_ids),
                    )
                )
            ).all()
        )
        tool_names = {definition.id: definition.name for definition in definitions}

    total = _ToolAccumulator()
    groups: dict[UUID, _ToolAccumulator] = {}
    for execution in executions:
        _record_tool(total, execution)
        _record_tool(groups.setdefault(execution.tool_id, _ToolAccumulator()), execution)

    tools: list[ToolMetric] = []
    for tool_id in sorted(groups, key=str):
        accumulator = groups[tool_id]
        average_duration = (
            accumulator.duration_total_ms / accumulator.duration_samples
            if accumulator.duration_samples
            else None
        )
        tools.append(
            ToolMetric(
                tool_id=tool_id,
                tool_name=tool_names.get(tool_id, "unknown"),
                executions=accumulator.executions,
                succeeded=accumulator.succeeded,
                failed=accumulator.failed,
                denied=accumulator.denied,
                approval_required=accumulator.approval_required,
                average_duration_ms=average_duration,
                success_rate=_tool_success_rate(accumulator),
            )
        )
    return ToolMetricsResponse(
        start=window.start,
        end=window.end,
        executions=total.executions,
        succeeded=total.succeeded,
        failed=total.failed,
        denied=total.denied,
        approval_required=total.approval_required,
        success_rate=_tool_success_rate(total),
        tools=tools,
    )


async def _load_operational_overview(
    db: AsyncSession,
    tenant_id: UUID,
    window: ReportWindow,
) -> OperationalOverview:
    created_conversations = list(
        (
            await db.scalars(
                select(Conversation).where(
                    Conversation.tenant_id == tenant_id,
                    Conversation.created_at >= window.start,
                    Conversation.created_at < window.end,
                )
            )
        ).all()
    )
    messages = list(
        (
            await db.scalars(
                select(Message)
                .where(
                    Message.tenant_id == tenant_id,
                    Message.occurred_at >= window.start,
                    Message.occurred_at < window.end,
                )
                .order_by(Message.conversation_id, Message.occurred_at, Message.created_at, Message.id)
            )
        ).all()
    )
    inbound_by_conversation: dict[UUID, list[Message]] = {}
    outbound_ai_conversations: set[UUID] = set()
    outbound_human_conversations: set[UUID] = set()
    all_messages_by_conversation: dict[UUID, list[Message]] = {}
    for message in messages:
        all_messages_by_conversation.setdefault(message.conversation_id, []).append(message)
        if (
            message.direction == MessageDirection.INBOUND.value
            and message.author_type == MessageAuthorType.CUSTOMER.value
        ):
            inbound_by_conversation.setdefault(message.conversation_id, []).append(message)
        elif message.direction == MessageDirection.OUTBOUND.value:
            if message.author_type == MessageAuthorType.AI.value:
                outbound_ai_conversations.add(message.conversation_id)
            elif message.author_type == MessageAuthorType.HUMAN.value:
                outbound_human_conversations.add(message.conversation_id)

    active_conversation_ids = set(inbound_by_conversation)
    active_conversations: list[Conversation] = []
    if active_conversation_ids:
        active_conversations = list(
            (
                await db.scalars(
                    select(Conversation).where(
                        Conversation.tenant_id == tenant_id,
                        Conversation.id.in_(active_conversation_ids),
                    )
                )
            ).all()
        )
    unique_contacts = len({conversation.contact_id for conversation in active_conversations})

    first_response_seconds: list[float] = []
    for conversation_id, inbound_messages in inbound_by_conversation.items():
        first_inbound = inbound_messages[0]
        for message in all_messages_by_conversation.get(conversation_id, []):
            if message.occurred_at < first_inbound.occurred_at:
                continue
            if (
                message.direction == MessageDirection.OUTBOUND.value
                and message.author_type
                in {MessageAuthorType.AI.value, MessageAuthorType.HUMAN.value}
            ):
                first_response_seconds.append(
                    max(0.0, (message.occurred_at - first_inbound.occurred_at).total_seconds())
                )
                break

    resolved_conversations = list(
        (
            await db.scalars(
                select(Conversation).where(
                    Conversation.tenant_id == tenant_id,
                    Conversation.status.in_(
                        [ConversationStatus.RESOLVED.value, ConversationStatus.CLOSED.value]
                    ),
                    Conversation.updated_at >= window.start,
                    Conversation.updated_at < window.end,
                )
            )
        ).all()
    )
    resolution_seconds = [
        max(0.0, (conversation.updated_at - conversation.created_at).total_seconds())
        for conversation in resolved_conversations
    ]

    handoffs = list(
        (
            await db.scalars(
                select(ConversationHandoff).where(
                    ConversationHandoff.tenant_id == tenant_id,
                    ConversationHandoff.requested_at >= window.start,
                    ConversationHandoff.requested_at < window.end,
                )
            )
        ).all()
    )
    handoff_conversation_ids = {handoff.conversation_id for handoff in handoffs}
    handoff_durations = [
        max(0.0, (handoff.closed_at - handoff.requested_at).total_seconds())
        for handoff in handoffs
        if handoff.closed_at is not None
    ]

    active_count = len(active_conversation_ids)
    handoff_count = len(active_conversation_ids & handoff_conversation_ids)
    automated_ids = (
        active_conversation_ids
        & outbound_ai_conversations
        - outbound_human_conversations
        - handoff_conversation_ids
    )
    return OperationalOverview(
        conversations=len(created_conversations),
        unique_contacts=unique_contacts,
        first_response_average_seconds=(
            sum(first_response_seconds) / len(first_response_seconds)
            if first_response_seconds
            else None
        ),
        first_response_samples=len(first_response_seconds),
        resolution_average_seconds=(
            sum(resolution_seconds) / len(resolution_seconds) if resolution_seconds else None
        ),
        resolution_samples=len(resolution_seconds),
        handoff_rate=handoff_count / active_count if active_count else None,
        handoff_conversations=handoff_count,
        handoff_average_seconds=(
            sum(handoff_durations) / len(handoff_durations) if handoff_durations else None
        ),
        handoff_duration_samples=len(handoff_durations),
        ai_automation_rate=len(automated_ids) / active_count if active_count else None,
        automated_conversations=len(automated_ids),
        active_conversations=active_count,
    )


@router.get("/pricing", response_model=list[PricingResponse])
async def list_pricing(
    context: TenantContextDependency,
    db: DbSession,
) -> list[PricingResponse]:
    rows = list(
        (
            await db.scalars(
                select(AIModelPricing)
                .where(AIModelPricing.tenant_id == context.tenant.id)
                .order_by(
                    AIModelPricing.provider,
                    AIModelPricing.model_id,
                    AIModelPricing.effective_from.desc(),
                )
            )
        ).all()
    )
    return [_pricing_response(row) for row in rows]


@router.put("/pricing", response_model=PricingResponse)
async def put_pricing(
    payload: PricingPut,
    context: TenantContextDependency,
    db: DbSession,
) -> PricingResponse:
    require_tenant_role(context, PricingWriteRoles)
    row = await db.scalar(
        select(AIModelPricing).where(
            AIModelPricing.tenant_id == context.tenant.id,
            AIModelPricing.provider == payload.provider,
            AIModelPricing.model_id == payload.model_id,
            AIModelPricing.effective_from == payload.effective_from,
        )
    )
    if row is None:
        row = AIModelPricing(
            tenant_id=context.tenant.id,
            provider=payload.provider,
            model_id=payload.model_id,
            input_per_million_usd=payload.input_per_million_usd,
            output_per_million_usd=payload.output_per_million_usd,
            audio_per_minute_usd=payload.audio_per_minute_usd,
            effective_from=payload.effective_from,
            created_by_user_id=context.current.user.id,
        )
        db.add(row)
        action = "analytics.pricing.created"
    else:
        row.input_per_million_usd = payload.input_per_million_usd
        row.output_per_million_usd = payload.output_per_million_usd
        row.audio_per_minute_usd = payload.audio_per_minute_usd
        action = "analytics.pricing.updated"
    await db.flush()
    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action=action,
            target_type="ai_model_pricing",
            target_id=row.id,
            details={
                "provider": row.provider,
                "model_id": row.model_id,
                "effective_from": row.effective_from.isoformat(),
                "input_per_million_usd": str(row.input_per_million_usd),
                "output_per_million_usd": str(row.output_per_million_usd),
                "audio_per_minute_usd": str(row.audio_per_minute_usd),
            },
        )
    )
    await db.commit()
    await db.refresh(row)
    return _pricing_response(row)


@router.get("/ai-usage", response_model=AIUsageResponse)
async def get_ai_usage(
    context: TenantContextDependency,
    db: DbSession,
    window: AnalyticsWindow,
) -> AIUsageResponse:
    return await _load_ai_usage(db, context.tenant.id, window)


@router.get("/tools", response_model=ToolMetricsResponse)
async def get_tool_metrics(
    context: TenantContextDependency,
    db: DbSession,
    window: AnalyticsWindow,
) -> ToolMetricsResponse:
    return await _load_tool_metrics(db, context.tenant.id, window)


@router.get("/overview", response_model=AnalyticsOverviewResponse)
async def get_overview(
    context: TenantContextDependency,
    db: DbSession,
    window: AnalyticsWindow,
) -> AnalyticsOverviewResponse:
    operations = await _load_operational_overview(db, context.tenant.id, window)
    ai = await _load_ai_usage(db, context.tenant.id, window)
    tools = await _load_tool_metrics(db, context.tenant.id, window)
    return AnalyticsOverviewResponse(
        start=window.start,
        end=window.end,
        operations=operations,
        ai=ai,
        tools=tools,
    )
