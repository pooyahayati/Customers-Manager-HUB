from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from customers_manager_hub.channel_models import ChannelInboundEvent
from customers_manager_hub.database import AsyncSessionFactory
from customers_manager_hub.handoff_models import HandoffPolicy
from customers_manager_hub.models import Conversation, Message, MessageType
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
from customers_manager_hub.tool_models import ToolDefinition, ToolOperationType, ToolRiskLevel

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_RISK_ORDER = {
    ToolRiskLevel.LOW: 0,
    ToolRiskLevel.MEDIUM: 1,
    ToolRiskLevel.HIGH: 2,
    ToolRiskLevel.CRITICAL: 3,
}


class PolicyRuntimeError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class BusinessWindow:
    start_minute: int
    end_minute: int


@dataclass(frozen=True, slots=True)
class EffectiveTenantPolicy:
    enabled: bool
    revision: int
    timezone: str
    business_hours: dict[str, tuple[BusinessWindow, ...]]
    outside_business_hours_action: OutsideBusinessHoursAction
    autonomy_mode: PolicyAutonomyMode
    message_rules: dict[MessageType, PolicyMessageAction]
    handoff_keywords: tuple[str, ...]
    handoff_on_tool_approval: bool
    approval_min_risk: ToolRiskLevel
    require_approval_for_writes: bool


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    action: PolicyDecisionAction
    reason_code: str
    policy_revision: int


@dataclass(frozen=True, slots=True)
class ToolPolicyDecision:
    action: PolicyDecisionAction
    reason_code: str
    policy_revision: int
    approval_required: bool


def normalize_policy_timezone(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 100:
        raise ValueError("Policy timezone must be 1-100 characters")
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Policy timezone must be a valid IANA timezone") from exc
    return normalized


def _parse_clock(value: object) -> int:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ValueError("Business-hour times must use HH:MM")
    hour_raw, minute_raw = value.split(":", 1)
    if not hour_raw.isdigit() or not minute_raw.isdigit():
        raise ValueError("Business-hour times must use HH:MM")
    hour = int(hour_raw)
    minute = int(minute_raw)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("Business-hour time is out of range")
    return hour * 60 + minute


def normalize_business_hours(value: dict[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    unknown = set(value) - set(_WEEKDAYS)
    if unknown:
        raise ValueError("Business hours contain an invalid weekday")
    for weekday in _WEEKDAYS:
        raw_windows = value.get(weekday)
        if raw_windows is None:
            continue
        if not isinstance(raw_windows, list):
            raise ValueError("Business-hour weekday value must be a list")
        windows = cast(list[object], raw_windows)
        if len(windows) > 8:
            raise ValueError("Business hours support at most 8 windows per weekday")
        parsed: list[tuple[int, int, dict[str, str]]] = []
        for raw_window in windows:
            if not isinstance(raw_window, dict):
                raise ValueError("Business-hour window must be an object")
            mapping = cast(dict[object, object], raw_window)
            if set(mapping) != {"start", "end"}:
                raise ValueError("Business-hour window requires only start and end")
            start_raw = mapping.get("start")
            end_raw = mapping.get("end")
            start = _parse_clock(start_raw)
            end = _parse_clock(end_raw)
            if end <= start:
                raise ValueError("Business-hour end must be after start")
            assert isinstance(start_raw, str)
            assert isinstance(end_raw, str)
            parsed.append((start, end, {"start": start_raw, "end": end_raw}))
        parsed.sort(key=lambda item: (item[0], item[1]))
        previous_end = -1
        rendered: list[dict[str, str]] = []
        for start, end, window in parsed:
            if start < previous_end:
                raise ValueError("Business-hour windows must not overlap")
            previous_end = end
            rendered.append(window)
        if rendered:
            normalized[weekday] = rendered
    return normalized


def normalize_message_rules(value: dict[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for raw_type, raw_action in value.items():
        try:
            message_type = MessageType(raw_type)
        except ValueError as exc:
            raise ValueError("Message policy contains an unsupported message type") from exc
        if not isinstance(raw_action, str):
            raise ValueError("Message policy action must be a string")
        try:
            action = PolicyMessageAction(raw_action)
        except ValueError as exc:
            raise ValueError("Message policy action is invalid") from exc
        normalized[message_type.value] = action.value
    return normalized


def normalize_handoff_keywords(value: list[str]) -> list[str]:
    if len(value) > 50:
        raise ValueError("At most 50 handoff keywords are supported")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in value:
        item = raw.strip().casefold()
        if not item or len(item) > 80:
            raise ValueError("Handoff keywords must be 1-80 characters")
        if item not in seen:
            seen.add(item)
            normalized.append(item)
    return normalized


def _parse_business_hours(value: dict[str, object]) -> dict[str, tuple[BusinessWindow, ...]]:
    normalized = normalize_business_hours(value)
    parsed: dict[str, tuple[BusinessWindow, ...]] = {}
    for weekday, raw_windows in normalized.items():
        windows = cast(list[dict[str, str]], raw_windows)
        parsed[weekday] = tuple(
            BusinessWindow(
                start_minute=_parse_clock(window["start"]),
                end_minute=_parse_clock(window["end"]),
            )
            for window in windows
        )
    return parsed


def _parse_message_rules(value: dict[str, object]) -> dict[MessageType, PolicyMessageAction]:
    normalized = normalize_message_rules(value)
    return {
        MessageType(message_type): PolicyMessageAction(cast(str, action))
        for message_type, action in normalized.items()
    }


def _policy_from_row(row: TenantPolicy) -> EffectiveTenantPolicy:
    try:
        timezone = normalize_policy_timezone(row.timezone)
        business_hours = _parse_business_hours(dict(row.business_hours))
        message_rules = _parse_message_rules(dict(row.message_rules))
        keywords = tuple(normalize_handoff_keywords(list(row.handoff_keywords)))
        outside_action = OutsideBusinessHoursAction(row.outside_business_hours_action)
        autonomy_mode = PolicyAutonomyMode(row.autonomy_mode)
        approval_min_risk = ToolRiskLevel(row.approval_min_risk)
    except (TypeError, ValueError) as exc:
        raise PolicyRuntimeError("policy_configuration_invalid") from exc
    return EffectiveTenantPolicy(
        enabled=row.enabled,
        revision=row.revision,
        timezone=timezone,
        business_hours=business_hours,
        outside_business_hours_action=outside_action,
        autonomy_mode=autonomy_mode,
        message_rules=message_rules,
        handoff_keywords=keywords,
        handoff_on_tool_approval=row.handoff_on_tool_approval,
        approval_min_risk=approval_min_risk,
        require_approval_for_writes=row.require_approval_for_writes,
    )


def _default_policy(legacy: HandoffPolicy | None = None) -> EffectiveTenantPolicy:
    keywords: tuple[str, ...] = ()
    handoff_on_tool_approval = True
    enabled = True
    if legacy is not None:
        try:
            keywords = tuple(normalize_handoff_keywords(list(legacy.customer_keywords)))
        except (TypeError, ValueError) as exc:
            raise PolicyRuntimeError("policy_configuration_invalid") from exc
        handoff_on_tool_approval = legacy.pause_on_tool_approval
        enabled = legacy.enabled
    return EffectiveTenantPolicy(
        enabled=enabled,
        revision=0,
        timezone="UTC",
        business_hours={},
        outside_business_hours_action=OutsideBusinessHoursAction.ALLOW,
        autonomy_mode=PolicyAutonomyMode.AUTONOMOUS,
        message_rules={},
        handoff_keywords=keywords,
        handoff_on_tool_approval=handoff_on_tool_approval,
        approval_min_risk=ToolRiskLevel.HIGH,
        require_approval_for_writes=False,
    )


async def load_effective_tenant_policy(
    session_factory: AsyncSessionFactory,
    tenant_id: UUID,
) -> EffectiveTenantPolicy:
    async with session_factory() as db:
        row = await db.scalar(select(TenantPolicy).where(TenantPolicy.tenant_id == tenant_id))
        if row is not None:
            return _policy_from_row(row)
        legacy = await db.scalar(select(HandoffPolicy).where(HandoffPolicy.tenant_id == tenant_id))
    return _default_policy(legacy)


def is_within_business_hours(
    policy: EffectiveTenantPolicy,
    evaluated_at: datetime | None = None,
) -> bool:
    if not policy.business_hours:
        return True
    instant = evaluated_at or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    local = instant.astimezone(ZoneInfo(policy.timezone))
    weekday = _WEEKDAYS[local.weekday()]
    minute = local.hour * 60 + local.minute
    return any(
        window.start_minute <= minute < window.end_minute
        for window in policy.business_hours.get(weekday, ())
    )


class PolicyEngine:
    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def _trace(
        self,
        *,
        tenant_id: UUID,
        policy_revision: int,
        decision_type: PolicyDecisionType,
        action: PolicyDecisionAction,
        reason_code: str,
        conversation_id: UUID | None = None,
        message_id: UUID | None = None,
        agent_id: UUID | None = None,
        tool_id: UUID | None = None,
        safe_context: dict[str, object] | None = None,
    ) -> None:
        context = safe_context or {}
        if len(context) > 20:
            raise PolicyRuntimeError("policy_trace_context_invalid")
        async with self._session_factory() as db:
            db.add(
                PolicyDecisionTrace(
                    tenant_id=tenant_id,
                    policy_revision=policy_revision,
                    decision_type=decision_type.value,
                    action=action.value,
                    reason_code=reason_code[:100],
                    conversation_id=conversation_id,
                    message_id=message_id,
                    agent_id=agent_id,
                    tool_id=tool_id,
                    safe_context=context,
                )
            )
            await db.commit()

    async def evaluate_message_event(self, event_id: UUID) -> PolicyDecision:
        async with self._session_factory() as db:
            event = await db.scalar(select(ChannelInboundEvent).where(ChannelInboundEvent.id == event_id))
            if event is None:
                raise PolicyRuntimeError("policy_event_missing")
            if event.message_id is None:
                return PolicyDecision(PolicyDecisionAction.ALLOW, "event_has_no_message", 0)
            message = await db.scalar(
                select(Message).where(
                    Message.id == event.message_id,
                    Message.tenant_id == event.tenant_id,
                )
            )
            if message is None:
                raise PolicyRuntimeError("policy_message_missing")
            tenant_id = event.tenant_id
            message_id = message.id
            conversation_id = message.conversation_id
            try:
                message_type = MessageType(message.message_type)
            except ValueError as exc:
                raise PolicyRuntimeError("policy_message_type_invalid") from exc

        policy = await load_effective_tenant_policy(self._session_factory, tenant_id)
        if not policy.enabled:
            action = PolicyDecisionAction.ALLOW
            reason = "policy_disabled"
        else:
            configured = policy.message_rules.get(message_type, PolicyMessageAction.ALLOW)
            if configured == PolicyMessageAction.DENY:
                action = PolicyDecisionAction.DENY
                reason = "message_policy_denied"
            elif configured == PolicyMessageAction.HANDOFF:
                action = PolicyDecisionAction.HANDOFF
                reason = "message_policy_handoff"
            else:
                action = PolicyDecisionAction.ALLOW
                reason = "message_policy_allowed"
        await self._trace(
            tenant_id=tenant_id,
            policy_revision=policy.revision,
            decision_type=PolicyDecisionType.MESSAGE,
            action=action,
            reason_code=reason,
            conversation_id=conversation_id,
            message_id=message_id,
            safe_context={"message_type": message_type.value},
        )
        return PolicyDecision(action, reason, policy.revision)

    async def evaluate_ai_event(
        self,
        event_id: UUID,
        *,
        evaluated_at: datetime | None = None,
    ) -> PolicyDecision:
        async with self._session_factory() as db:
            event = await db.scalar(select(ChannelInboundEvent).where(ChannelInboundEvent.id == event_id))
            if event is None:
                raise PolicyRuntimeError("policy_event_missing")
            if event.message_id is None:
                return PolicyDecision(PolicyDecisionAction.ALLOW, "event_has_no_message", 0)
            message = await db.scalar(
                select(Message).where(
                    Message.id == event.message_id,
                    Message.tenant_id == event.tenant_id,
                )
            )
            if message is None:
                raise PolicyRuntimeError("policy_message_missing")
            conversation = await db.scalar(
                select(Conversation).where(
                    Conversation.id == message.conversation_id,
                    Conversation.tenant_id == event.tenant_id,
                )
            )
            if conversation is None:
                raise PolicyRuntimeError("policy_conversation_missing")
            tenant_id = event.tenant_id
            message_id = message.id
            conversation_id = conversation.id
            message_text = (message.text or "").casefold()

        policy = await load_effective_tenant_policy(self._session_factory, tenant_id)
        if not policy.enabled:
            await self._trace(
                tenant_id=tenant_id,
                policy_revision=policy.revision,
                decision_type=PolicyDecisionType.AUTONOMY,
                action=PolicyDecisionAction.ALLOW,
                reason_code="policy_disabled",
                conversation_id=conversation_id,
                message_id=message_id,
            )
            return PolicyDecision(PolicyDecisionAction.ALLOW, "policy_disabled", policy.revision)

        if policy.business_hours:
            within_hours = is_within_business_hours(policy, evaluated_at)
            if not within_hours and policy.outside_business_hours_action in {
                OutsideBusinessHoursAction.HANDOFF,
                OutsideBusinessHoursAction.HUMAN_ONLY,
            }:
                reason = (
                    "outside_business_hours_human_only"
                    if policy.outside_business_hours_action == OutsideBusinessHoursAction.HUMAN_ONLY
                    else "outside_business_hours_handoff"
                )
                await self._trace(
                    tenant_id=tenant_id,
                    policy_revision=policy.revision,
                    decision_type=PolicyDecisionType.BUSINESS_HOURS,
                    action=PolicyDecisionAction.HANDOFF,
                    reason_code=reason,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    safe_context={"timezone": policy.timezone},
                )
                return PolicyDecision(PolicyDecisionAction.HANDOFF, reason, policy.revision)
            await self._trace(
                tenant_id=tenant_id,
                policy_revision=policy.revision,
                decision_type=PolicyDecisionType.BUSINESS_HOURS,
                action=PolicyDecisionAction.ALLOW,
                reason_code=("within_business_hours" if within_hours else "outside_business_hours_allowed"),
                conversation_id=conversation_id,
                message_id=message_id,
                safe_context={"timezone": policy.timezone},
            )

        if policy.autonomy_mode != PolicyAutonomyMode.AUTONOMOUS:
            reason = (
                "autonomy_assist_only"
                if policy.autonomy_mode == PolicyAutonomyMode.ASSIST_ONLY
                else "autonomy_human_only"
            )
            await self._trace(
                tenant_id=tenant_id,
                policy_revision=policy.revision,
                decision_type=PolicyDecisionType.AUTONOMY,
                action=PolicyDecisionAction.HANDOFF,
                reason_code=reason,
                conversation_id=conversation_id,
                message_id=message_id,
                safe_context={"autonomy_mode": policy.autonomy_mode.value},
            )
            return PolicyDecision(PolicyDecisionAction.HANDOFF, reason, policy.revision)

        matched_keyword = next(
            (keyword for keyword in policy.handoff_keywords if keyword in message_text),
            None,
        )
        if matched_keyword is not None:
            await self._trace(
                tenant_id=tenant_id,
                policy_revision=policy.revision,
                decision_type=PolicyDecisionType.HANDOFF,
                action=PolicyDecisionAction.HANDOFF,
                reason_code="handoff_keyword",
                conversation_id=conversation_id,
                message_id=message_id,
                safe_context={"matched_keyword": matched_keyword},
            )
            return PolicyDecision(PolicyDecisionAction.HANDOFF, "handoff_keyword", policy.revision)

        await self._trace(
            tenant_id=tenant_id,
            policy_revision=policy.revision,
            decision_type=PolicyDecisionType.AUTONOMY,
            action=PolicyDecisionAction.ALLOW,
            reason_code="autonomous_ai_allowed",
            conversation_id=conversation_id,
            message_id=message_id,
            safe_context={"autonomy_mode": policy.autonomy_mode.value},
        )
        return PolicyDecision(PolicyDecisionAction.ALLOW, "autonomous_ai_allowed", policy.revision)

    async def evaluate_tool(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        tool: ToolDefinition,
        baseline_approval_required: bool,
        conversation_id: UUID | None = None,
    ) -> ToolPolicyDecision:
        if tool.tenant_id != tenant_id:
            raise PolicyRuntimeError("policy_tool_tenant_mismatch")
        policy = await load_effective_tenant_policy(self._session_factory, tenant_id)
        async with self._session_factory() as db:
            rule = await db.scalar(
                select(ToolPolicyRule).where(
                    ToolPolicyRule.tenant_id == tenant_id,
                    ToolPolicyRule.tool_id == tool.id,
                )
            )

        if policy.enabled and rule is not None and rule.effect == PolicyToolEffect.DENY.value:
            action = PolicyDecisionAction.DENY
            reason = "tool_policy_denied"
            approval_required = False
        else:
            approval_required = baseline_approval_required
            reason = "tool_policy_allowed"
            if policy.enabled:
                risk = ToolRiskLevel(tool.risk_level)
                configured_floor = policy.approval_min_risk
                if _RISK_ORDER[risk] >= _RISK_ORDER[configured_floor]:
                    approval_required = True
                    reason = "tool_policy_risk_approval"
                if (
                    policy.require_approval_for_writes
                    and tool.operation_type == ToolOperationType.WRITE.value
                ):
                    approval_required = True
                    reason = "tool_policy_write_approval"
                if rule is not None and rule.approval_mode == PolicyApprovalMode.REQUIRED.value:
                    approval_required = True
                    reason = "tool_policy_rule_approval"
            if approval_required:
                action = PolicyDecisionAction.APPROVAL_REQUIRED
                if reason == "tool_policy_allowed":
                    reason = "tool_baseline_approval"
            else:
                action = PolicyDecisionAction.ALLOW

        safe_context: dict[str, object] = {
            "risk_level": tool.risk_level,
            "operation_type": tool.operation_type,
        }
        if rule is not None:
            safe_context["rule_effect"] = rule.effect
            safe_context["approval_mode"] = rule.approval_mode
        await self._trace(
            tenant_id=tenant_id,
            policy_revision=policy.revision,
            decision_type=PolicyDecisionType.TOOL,
            action=action,
            reason_code=reason,
            conversation_id=conversation_id,
            agent_id=agent_id,
            tool_id=tool.id,
            safe_context=safe_context,
        )
        return ToolPolicyDecision(
            action=action,
            reason_code=reason,
            policy_revision=policy.revision,
            approval_required=approval_required,
        )
