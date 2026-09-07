from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from customers_manager_hub.agent_models import AgentRun, AgentRunStatus
from customers_manager_hub.channel_models import ChannelInboundEvent
from customers_manager_hub.database import AsyncSessionFactory
from customers_manager_hub.handoff_models import (
    ConversationHandoff,
    HandoffPolicy,
    HandoffRequestSource,
    HandoffStatus,
)
from customers_manager_hub.models import (
    AuditEvent,
    Conversation,
    Message,
    MessageAuthorType,
    MessageDirection,
)


class HandoffRuntimeError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class EffectiveHandoffPolicy:
    enabled: bool
    customer_keywords: tuple[str, ...]
    pause_on_tool_approval: bool


@dataclass(frozen=True, slots=True)
class HandoffResolution:
    handoff_id: UUID
    event_id_to_resume: UUID | None


def default_handoff_policy() -> EffectiveHandoffPolicy:
    return EffectiveHandoffPolicy(
        enabled=True,
        customer_keywords=(),
        pause_on_tool_approval=True,
    )


async def load_effective_policy(
    session_factory: AsyncSessionFactory,
    tenant_id: UUID,
) -> EffectiveHandoffPolicy:
    async with session_factory() as db:
        row = await db.scalar(select(HandoffPolicy).where(HandoffPolicy.tenant_id == tenant_id))
    if row is None:
        return default_handoff_policy()
    keywords = tuple(
        item.strip().casefold()
        for item in row.customer_keywords
        if isinstance(item, str) and item.strip()
    )
    return EffectiveHandoffPolicy(
        enabled=row.enabled,
        customer_keywords=keywords,
        pause_on_tool_approval=row.pause_on_tool_approval,
    )


async def _active_handoff(
    db,
    tenant_id: UUID,
    conversation_id: UUID,
    *,
    for_update: bool = False,
) -> ConversationHandoff | None:
    statement = select(ConversationHandoff).where(
        ConversationHandoff.tenant_id == tenant_id,
        ConversationHandoff.conversation_id == conversation_id,
        ConversationHandoff.status.in_((HandoffStatus.QUEUED.value, HandoffStatus.CLAIMED.value)),
    )
    if for_update:
        statement = statement.with_for_update()
    return await db.scalar(statement)


async def is_conversation_ai_paused(
    session_factory: AsyncSessionFactory,
    tenant_id: UUID,
    conversation_id: UUID,
) -> bool:
    async with session_factory() as db:
        return await _active_handoff(db, tenant_id, conversation_id) is not None


async def is_event_ai_paused(
    session_factory: AsyncSessionFactory,
    event_id: UUID,
) -> bool:
    async with session_factory() as db:
        event = await db.scalar(select(ChannelInboundEvent).where(ChannelInboundEvent.id == event_id))
        if event is None or event.message_id is None:
            return False
        message = await db.scalar(
            select(Message).where(
                Message.id == event.message_id,
                Message.tenant_id == event.tenant_id,
            )
        )
        if message is None:
            return False
        return await _active_handoff(db, event.tenant_id, message.conversation_id) is not None


async def request_handoff(
    session_factory: AsyncSessionFactory,
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    request_source: HandoffRequestSource,
    reason_code: str,
    reason_text: str | None = None,
    requested_by_user_id: UUID | None = None,
    source_agent_run_id: UUID | None = None,
    source_tool_execution_id: UUID | None = None,
) -> ConversationHandoff:
    for attempt in range(2):
        async with session_factory() as db:
            conversation = await db.scalar(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    Conversation.tenant_id == tenant_id,
                )
            )
            if conversation is None:
                raise HandoffRuntimeError("handoff_conversation_missing")
            existing = await _active_handoff(db, tenant_id, conversation_id, for_update=True)
            if existing is not None:
                changed = False
                if existing.source_agent_run_id is None and source_agent_run_id is not None:
                    existing.source_agent_run_id = source_agent_run_id
                    changed = True
                if existing.source_tool_execution_id is None and source_tool_execution_id is not None:
                    existing.source_tool_execution_id = source_tool_execution_id
                    changed = True
                if changed:
                    await db.commit()
                    await db.refresh(existing)
                return existing

            handoff = ConversationHandoff(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                status=HandoffStatus.QUEUED.value,
                request_source=request_source.value,
                reason_code=reason_code[:100],
                reason_text=reason_text[:1000] if reason_text is not None else None,
                requested_by_user_id=requested_by_user_id,
                source_agent_run_id=source_agent_run_id,
                source_tool_execution_id=source_tool_execution_id,
            )
            db.add(handoff)
            try:
                await db.flush()
                db.add(
                    AuditEvent(
                        tenant_id=tenant_id,
                        actor_user_id=requested_by_user_id,
                        action="handoff.requested",
                        target_type="conversation_handoff",
                        target_id=handoff.id,
                        details={
                            "conversation_id": str(conversation_id),
                            "request_source": request_source.value,
                            "reason_code": handoff.reason_code,
                        },
                    )
                )
                await db.commit()
            except IntegrityError:
                await db.rollback()
                if attempt == 0:
                    continue
                raise
            await db.refresh(handoff)
            return handoff
    raise HandoffRuntimeError("handoff_request_race")


async def evaluate_event_escalation(
    session_factory: AsyncSessionFactory,
    event_id: UUID,
) -> UUID | None:
    async with session_factory() as db:
        event = await db.scalar(select(ChannelInboundEvent).where(ChannelInboundEvent.id == event_id))
        if event is None or event.message_id is None:
            return None
        message = await db.scalar(
            select(Message).where(
                Message.id == event.message_id,
                Message.tenant_id == event.tenant_id,
            )
        )
        if message is None:
            return None
        existing = await _active_handoff(db, event.tenant_id, message.conversation_id)
        if existing is not None:
            return existing.id
        if (
            message.direction != MessageDirection.INBOUND.value
            or message.author_type != MessageAuthorType.CUSTOMER.value
            or message.text is None
            or not message.text.strip()
        ):
            return None
        tenant_id = event.tenant_id
        conversation_id = message.conversation_id
        text = message.text.casefold()

    policy = await load_effective_policy(session_factory, tenant_id)
    if not policy.enabled or not policy.customer_keywords:
        return None
    matched = next((keyword for keyword in policy.customer_keywords if keyword in text), None)
    if matched is None:
        return None
    handoff = await request_handoff(
        session_factory,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        request_source=HandoffRequestSource.SYSTEM,
        reason_code="customer_keyword",
        reason_text=f"Matched escalation keyword: {matched}",
    )
    return handoff.id


async def pause_for_tool_approval(
    session_factory: AsyncSessionFactory,
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    agent_run_id: UUID,
    tool_execution_id: UUID,
) -> UUID | None:
    policy = await load_effective_policy(session_factory, tenant_id)
    if not policy.enabled or not policy.pause_on_tool_approval:
        return None

    for attempt in range(2):
        async with session_factory() as db:
            run = await db.scalar(
                select(AgentRun)
                .where(
                    AgentRun.id == agent_run_id,
                    AgentRun.tenant_id == tenant_id,
                    AgentRun.conversation_id == conversation_id,
                )
                .with_for_update()
            )
            if run is None:
                raise HandoffRuntimeError("handoff_agent_run_missing")
            existing = await _active_handoff(db, tenant_id, conversation_id, for_update=True)
            created = False
            if existing is None:
                existing = ConversationHandoff(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    status=HandoffStatus.QUEUED.value,
                    request_source=HandoffRequestSource.TOOL.value,
                    reason_code="tool_approval_required",
                    source_agent_run_id=agent_run_id,
                    source_tool_execution_id=tool_execution_id,
                )
                db.add(existing)
                created = True
            else:
                if existing.source_agent_run_id is None:
                    existing.source_agent_run_id = agent_run_id
                if existing.source_tool_execution_id is None:
                    existing.source_tool_execution_id = tool_execution_id

            run.status = AgentRunStatus.PAUSED.value
            run.generated_text = None
            run.error_code = "handoff_tool_approval_required"
            run.lease_token = None
            run.lease_until = None
            try:
                await db.flush()
                if created:
                    db.add(
                        AuditEvent(
                            tenant_id=tenant_id,
                            actor_user_id=None,
                            action="handoff.requested",
                            target_type="conversation_handoff",
                            target_id=existing.id,
                            details={
                                "conversation_id": str(conversation_id),
                                "request_source": HandoffRequestSource.TOOL.value,
                                "reason_code": "tool_approval_required",
                                "tool_execution_id": str(tool_execution_id),
                            },
                        )
                    )
                await db.commit()
            except IntegrityError:
                await db.rollback()
                if attempt == 0:
                    continue
                raise
            return existing.id
    raise HandoffRuntimeError("handoff_tool_pause_race")


async def claim_handoff(
    session_factory: AsyncSessionFactory,
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    actor_user_id: UUID,
) -> ConversationHandoff:
    async with session_factory() as db:
        handoff = await _active_handoff(db, tenant_id, conversation_id, for_update=True)
        if handoff is None:
            raise HandoffRuntimeError("handoff_active_missing")
        if handoff.status == HandoffStatus.CLAIMED.value:
            if handoff.claimed_by_user_id == actor_user_id:
                return handoff
            raise HandoffRuntimeError("handoff_already_claimed")
        if handoff.status != HandoffStatus.QUEUED.value:
            raise HandoffRuntimeError("handoff_state_invalid")
        now = datetime.now(UTC)
        handoff.status = HandoffStatus.CLAIMED.value
        handoff.claimed_by_user_id = actor_user_id
        handoff.claimed_at = now
        db.add(
            AuditEvent(
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                action="handoff.claimed",
                target_type="conversation_handoff",
                target_id=handoff.id,
                details={"conversation_id": str(conversation_id)},
            )
        )
        await db.commit()
        await db.refresh(handoff)
        return handoff


async def release_handoff(
    session_factory: AsyncSessionFactory,
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    actor_user_id: UUID,
    allow_override: bool,
) -> ConversationHandoff:
    async with session_factory() as db:
        handoff = await _active_handoff(db, tenant_id, conversation_id, for_update=True)
        if handoff is None:
            raise HandoffRuntimeError("handoff_active_missing")
        if handoff.status != HandoffStatus.CLAIMED.value:
            raise HandoffRuntimeError("handoff_not_claimed")
        if handoff.claimed_by_user_id != actor_user_id and not allow_override:
            raise HandoffRuntimeError("handoff_not_owner")
        handoff.status = HandoffStatus.QUEUED.value
        handoff.claimed_by_user_id = None
        handoff.claimed_at = None
        db.add(
            AuditEvent(
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                action="handoff.released",
                target_type="conversation_handoff",
                target_id=handoff.id,
                details={"conversation_id": str(conversation_id)},
            )
        )
        await db.commit()
        await db.refresh(handoff)
        return handoff


async def resolve_handoff(
    session_factory: AsyncSessionFactory,
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    actor_user_id: UUID,
    allow_override: bool,
    resume_ai: bool,
    cancel: bool = False,
) -> HandoffResolution:
    async with session_factory() as db:
        handoff = await _active_handoff(db, tenant_id, conversation_id, for_update=True)
        if handoff is None:
            handoff = await db.scalar(
                select(ConversationHandoff)
                .where(
                    ConversationHandoff.tenant_id == tenant_id,
                    ConversationHandoff.conversation_id == conversation_id,
                    ConversationHandoff.status == HandoffStatus.RESOLVED.value,
                )
                .order_by(ConversationHandoff.closed_at.desc(), ConversationHandoff.id.desc())
                .limit(1)
            )
            if handoff is None or not resume_ai:
                raise HandoffRuntimeError("handoff_active_missing")
        else:
            if (
                handoff.status == HandoffStatus.CLAIMED.value
                and handoff.claimed_by_user_id != actor_user_id
                and not allow_override
            ):
                raise HandoffRuntimeError("handoff_not_owner")
            handoff.status = (
                HandoffStatus.CANCELLED.value if cancel else HandoffStatus.RESOLVED.value
            )
            handoff.closed_at = datetime.now(UTC)
            db.add(
                AuditEvent(
                    tenant_id=tenant_id,
                    actor_user_id=actor_user_id,
                    action="handoff.cancelled" if cancel else "handoff.resolved",
                    target_type="conversation_handoff",
                    target_id=handoff.id,
                    details={
                        "conversation_id": str(conversation_id),
                        "resume_ai": resume_ai,
                    },
                )
            )

        event_id: UUID | None = None
        if resume_ai and handoff.source_agent_run_id is not None:
            run = await db.scalar(
                select(AgentRun)
                .where(
                    AgentRun.id == handoff.source_agent_run_id,
                    AgentRun.tenant_id == tenant_id,
                    AgentRun.conversation_id == conversation_id,
                )
                .with_for_update()
            )
            if run is not None and run.status in {
                AgentRunStatus.PAUSED.value,
                AgentRunStatus.PENDING.value,
            }:
                run.status = AgentRunStatus.PENDING.value
                run.generated_text = None
                run.error_code = None
                run.lease_token = None
                run.lease_until = None
                event = await db.scalar(
                    select(ChannelInboundEvent)
                    .where(
                        ChannelInboundEvent.tenant_id == tenant_id,
                        ChannelInboundEvent.message_id == run.inbound_message_id,
                    )
                    .order_by(ChannelInboundEvent.created_at.desc(), ChannelInboundEvent.id.desc())
                    .limit(1)
                )
                if event is not None:
                    event_id = event.id
        await db.commit()
        return HandoffResolution(handoff_id=handoff.id, event_id_to_resume=event_id)
