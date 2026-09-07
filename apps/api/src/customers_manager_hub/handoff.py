from datetime import datetime
from enum import StrEnum
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.agent_models import (
    Agent,
    AgentChannelAssignment,
    AgentPromptVersion,
    PromptVersionStatus,
)
from customers_manager_hub.agent_runtime import (
    build_conversation_input,
    compose_instructions,
    validate_customer_response,
)
from customers_manager_hub.ai_gateway import AIGateway, AIRoutingError, GenerationRequest
from customers_manager_hub.ai_models import AITaskType
from customers_manager_hub.channel_gateway import ChannelRegistry
from customers_manager_hub.channel_models import (
    ChannelAccount,
    ChannelType,
    ConversationChannelBinding,
)
from customers_manager_hub.channel_queue import ChannelJobQueue
from customers_manager_hub.channel_runtime import ChannelRuntimeError, dispatch_text
from customers_manager_hub.config import Settings, get_settings
from customers_manager_hub.database import AsyncSessionFactory, get_db_session
from customers_manager_hub.handoff_models import (
    ConversationHandoff,
    HandoffPolicy,
    HandoffRequestSource,
    HandoffStatus,
    OperatorAssistSuggestion,
)
from customers_manager_hub.handoff_runtime import (
    HandoffRuntimeError,
    claim_handoff,
    default_handoff_policy,
    release_handoff,
    request_handoff,
    resolve_handoff,
)
from customers_manager_hub.models import (
    AuditEvent,
    Contact,
    Conversation,
    ConversationStatus,
    Message,
    MessageAuthorType,
    MessageDirection,
    MessageType,
    TenantRole,
)
from customers_manager_hub.tenants import TenantContextDependency, require_tenant_role

router = APIRouter(
    prefix="/api/v1/tenants/{tenant_id}/operator-inbox",
    tags=["operator-inbox"],
)
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]
OperatorRoles = frozenset(
    {TenantRole.OWNER, TenantRole.ADMIN, TenantRole.SUPERVISOR, TenantRole.AGENT}
)
OverrideRoles = frozenset({TenantRole.OWNER, TenantRole.ADMIN, TenantRole.SUPERVISOR})
PolicyWriteRoles = frozenset({TenantRole.OWNER, TenantRole.ADMIN})
_MAX_HISTORY = 200


class InboxAssignment(StrEnum):
    ALL = "all"
    MINE = "mine"
    UNASSIGNED = "unassigned"


class HandoffRequestPayload(BaseModel):
    reason_code: str = Field(default="manual", min_length=1, max_length=100)
    reason_text: str | None = Field(default=None, max_length=1000)

    @field_validator("reason_code")
    @classmethod
    def normalize_reason_code(cls, value: str) -> str:
        normalized = value.strip().casefold().replace(" ", "_")
        if not normalized:
            raise ValueError("reason_code must not be blank")
        return normalized

    @field_validator("reason_text")
    @classmethod
    def normalize_reason_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ResolvePayload(BaseModel):
    resume_ai: bool = False
    cancel: bool = False


class HumanReplyPayload(BaseModel):
    client_message_id: UUID
    text: str = Field(min_length=1, max_length=4096)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Reply text must not be blank")
        if "\x00" in normalized:
            raise ValueError("Reply text contains an invalid character")
        return normalized


class HandoffPolicyPut(BaseModel):
    enabled: bool = True
    customer_keywords: list[str] = Field(default_factory=list, max_length=50)
    pause_on_tool_approval: bool = True

    @field_validator("customer_keywords")
    @classmethod
    def normalize_keywords(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            item = raw.strip().casefold()
            if not item or len(item) > 80:
                raise ValueError("Escalation keywords must be 1-80 characters")
            if item not in seen:
                seen.add(item)
                normalized.append(item)
        return normalized


class HandoffPolicyResponse(BaseModel):
    enabled: bool
    customer_keywords: list[str]
    pause_on_tool_approval: bool
    updated_at: datetime | None


class HandoffResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    status: HandoffStatus
    request_source: HandoffRequestSource
    reason_code: str
    reason_text: str | None
    requested_by_user_id: UUID | None
    claimed_by_user_id: UUID | None
    source_agent_run_id: UUID | None
    source_tool_execution_id: UUID | None
    requested_at: datetime
    claimed_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class InboxItemResponse(BaseModel):
    conversation_id: UUID
    contact_id: UUID
    contact_display_name: str | None
    conversation_status: ConversationStatus
    subject: str | None
    channel_type: ChannelType | None
    channel_account_id: UUID | None
    last_message_at: datetime | None
    last_message_preview: str | None
    handoff: HandoffResponse


class OperatorMessageResponse(BaseModel):
    id: UUID
    direction: MessageDirection
    author_type: MessageAuthorType
    message_type: MessageType
    text: str | None
    occurred_at: datetime
    created_at: datetime


class OperatorConversationResponse(BaseModel):
    conversation_id: UUID
    contact_id: UUID
    contact_display_name: str | None
    conversation_status: ConversationStatus
    subject: str | None
    channel_type: ChannelType | None
    channel_account_id: UUID | None
    handoff: HandoffResponse
    messages: list[OperatorMessageResponse]


class HumanReplyResponse(BaseModel):
    message_id: UUID
    text: str
    occurred_at: datetime


class AssistSuggestionResponse(BaseModel):
    id: UUID
    text: str
    created_at: datetime


def get_session_factory(request: Request) -> AsyncSessionFactory:
    return cast(AsyncSessionFactory, request.app.state.db_session_factory)


def get_channel_registry(request: Request) -> ChannelRegistry:
    return cast(ChannelRegistry, request.app.state.channel_registry)


def get_channel_queue(request: Request) -> ChannelJobQueue:
    return cast(ChannelJobQueue, request.app.state.channel_queue)


def get_ai_gateway(request: Request) -> AIGateway:
    return cast(AIGateway, request.app.state.ai_gateway)


def _handoff_response(handoff: ConversationHandoff) -> HandoffResponse:
    return HandoffResponse(
        id=handoff.id,
        conversation_id=handoff.conversation_id,
        status=HandoffStatus(handoff.status),
        request_source=HandoffRequestSource(handoff.request_source),
        reason_code=handoff.reason_code,
        reason_text=handoff.reason_text,
        requested_by_user_id=handoff.requested_by_user_id,
        claimed_by_user_id=handoff.claimed_by_user_id,
        source_agent_run_id=handoff.source_agent_run_id,
        source_tool_execution_id=handoff.source_tool_execution_id,
        requested_at=handoff.requested_at,
        claimed_at=handoff.claimed_at,
        closed_at=handoff.closed_at,
        created_at=handoff.created_at,
        updated_at=handoff.updated_at,
    )


def _runtime_http_error(exc: HandoffRuntimeError) -> HTTPException:
    if exc.code in {
        "handoff_conversation_missing",
        "handoff_active_missing",
        "handoff_agent_run_missing",
    }:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Handoff not found")
    if exc.code == "handoff_not_owner":
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Handoff is owned by another operator"
        )
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.code)


async def _load_conversation_contact_binding(
    db: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
) -> tuple[Conversation, Contact, ConversationChannelBinding | None, ChannelAccount | None]:
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == tenant_id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    contact = await db.scalar(
        select(Contact).where(
            Contact.id == conversation.contact_id,
            Contact.tenant_id == tenant_id,
        )
    )
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    binding = await db.scalar(
        select(ConversationChannelBinding).where(
            ConversationChannelBinding.tenant_id == tenant_id,
            ConversationChannelBinding.conversation_id == conversation_id,
        )
    )
    account: ChannelAccount | None = None
    if binding is not None:
        account = await db.scalar(
            select(ChannelAccount).where(
                ChannelAccount.id == binding.channel_account_id,
                ChannelAccount.tenant_id == tenant_id,
            )
        )
    return conversation, contact, binding, account


async def _load_active_handoff_api(
    db: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
) -> ConversationHandoff:
    handoff = await db.scalar(
        select(ConversationHandoff).where(
            ConversationHandoff.tenant_id == tenant_id,
            ConversationHandoff.conversation_id == conversation_id,
            ConversationHandoff.status.in_(
                (HandoffStatus.QUEUED.value, HandoffStatus.CLAIMED.value)
            ),
        )
    )
    if handoff is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Active handoff not found"
        )
    return handoff


def _can_override(role: TenantRole) -> bool:
    return role in OverrideRoles


def _ensure_reply_owner(
    handoff: ConversationHandoff,
    *,
    actor_user_id: UUID,
    role: TenantRole,
) -> None:
    if handoff.status != HandoffStatus.CLAIMED.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Handoff must be claimed")
    if handoff.claimed_by_user_id != actor_user_id and not _can_override(role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Handoff is owned by another operator"
        )


@router.get("/handoff-policy", response_model=HandoffPolicyResponse)
async def get_handoff_policy(
    context: TenantContextDependency,
    db: DbSession,
) -> HandoffPolicyResponse:
    policy = await db.scalar(
        select(HandoffPolicy).where(HandoffPolicy.tenant_id == context.tenant.id)
    )
    if policy is None:
        default = default_handoff_policy()
        return HandoffPolicyResponse(
            enabled=default.enabled,
            customer_keywords=list(default.customer_keywords),
            pause_on_tool_approval=default.pause_on_tool_approval,
            updated_at=None,
        )
    return HandoffPolicyResponse(
        enabled=policy.enabled,
        customer_keywords=list(policy.customer_keywords),
        pause_on_tool_approval=policy.pause_on_tool_approval,
        updated_at=policy.updated_at,
    )


@router.put("/handoff-policy", response_model=HandoffPolicyResponse)
async def put_handoff_policy(
    payload: HandoffPolicyPut,
    context: TenantContextDependency,
    db: DbSession,
) -> HandoffPolicyResponse:
    require_tenant_role(context, PolicyWriteRoles)
    policy = await db.scalar(
        select(HandoffPolicy).where(HandoffPolicy.tenant_id == context.tenant.id)
    )
    if policy is None:
        policy = HandoffPolicy(
            tenant_id=context.tenant.id,
            enabled=payload.enabled,
            customer_keywords=payload.customer_keywords,
            pause_on_tool_approval=payload.pause_on_tool_approval,
        )
        db.add(policy)
    else:
        policy.enabled = payload.enabled
        policy.customer_keywords = payload.customer_keywords
        policy.pause_on_tool_approval = payload.pause_on_tool_approval
    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="handoff.policy.updated",
            target_type="handoff_policy",
            target_id=None,
            details={
                "enabled": payload.enabled,
                "keyword_count": len(payload.customer_keywords),
                "pause_on_tool_approval": payload.pause_on_tool_approval,
            },
        )
    )
    await db.commit()
    await db.refresh(policy)
    return HandoffPolicyResponse(
        enabled=policy.enabled,
        customer_keywords=list(policy.customer_keywords),
        pause_on_tool_approval=policy.pause_on_tool_approval,
        updated_at=policy.updated_at,
    )


@router.get("", response_model=list[InboxItemResponse])
async def list_operator_inbox(
    context: TenantContextDependency,
    db: DbSession,
    assignment: Annotated[InboxAssignment, Query()] = InboxAssignment.ALL,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[InboxItemResponse]:
    statement = (
        select(ConversationHandoff)
        .where(
            ConversationHandoff.tenant_id == context.tenant.id,
            ConversationHandoff.status.in_(
                (HandoffStatus.QUEUED.value, HandoffStatus.CLAIMED.value)
            ),
        )
        .order_by(ConversationHandoff.requested_at, ConversationHandoff.id)
        .limit(limit)
    )
    if assignment == InboxAssignment.MINE:
        statement = statement.where(
            ConversationHandoff.claimed_by_user_id == context.current.user.id
        )
    elif assignment == InboxAssignment.UNASSIGNED:
        statement = statement.where(ConversationHandoff.status == HandoffStatus.QUEUED.value)
    handoffs = list((await db.scalars(statement)).all())
    items: list[InboxItemResponse] = []
    for handoff in handoffs:
        conversation, contact, binding, account = await _load_conversation_contact_binding(
            db, context.tenant.id, handoff.conversation_id
        )
        latest = await db.scalar(
            select(Message)
            .where(
                Message.tenant_id == context.tenant.id,
                Message.conversation_id == conversation.id,
            )
            .order_by(Message.occurred_at.desc(), Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
        preview = latest.text[:240] if latest is not None and latest.text is not None else None
        items.append(
            InboxItemResponse(
                conversation_id=conversation.id,
                contact_id=contact.id,
                contact_display_name=contact.display_name,
                conversation_status=ConversationStatus(conversation.status),
                subject=conversation.subject,
                channel_type=ChannelType(account.channel_type) if account is not None else None,
                channel_account_id=binding.channel_account_id if binding is not None else None,
                last_message_at=conversation.last_message_at,
                last_message_preview=preview,
                handoff=_handoff_response(handoff),
            )
        )
    return items


@router.get("/{conversation_id}", response_model=OperatorConversationResponse)
async def get_operator_conversation(
    conversation_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=_MAX_HISTORY)] = 100,
) -> OperatorConversationResponse:
    conversation, contact, binding, account = await _load_conversation_contact_binding(
        db, context.tenant.id, conversation_id
    )
    handoff = await _load_active_handoff_api(db, context.tenant.id, conversation_id)
    messages = list(
        (
            await db.scalars(
                select(Message)
                .where(
                    Message.tenant_id == context.tenant.id,
                    Message.conversation_id == conversation.id,
                )
                .order_by(Message.occurred_at, Message.created_at, Message.id)
                .limit(limit)
            )
        ).all()
    )
    return OperatorConversationResponse(
        conversation_id=conversation.id,
        contact_id=contact.id,
        contact_display_name=contact.display_name,
        conversation_status=ConversationStatus(conversation.status),
        subject=conversation.subject,
        channel_type=ChannelType(account.channel_type) if account is not None else None,
        channel_account_id=binding.channel_account_id if binding is not None else None,
        handoff=_handoff_response(handoff),
        messages=[
            OperatorMessageResponse(
                id=message.id,
                direction=MessageDirection(message.direction),
                author_type=MessageAuthorType(message.author_type),
                message_type=MessageType(message.message_type),
                text=message.text,
                occurred_at=message.occurred_at,
                created_at=message.created_at,
            )
            for message in messages
        ],
    )


@router.post("/{conversation_id}/handoff", response_model=HandoffResponse, status_code=201)
async def create_manual_handoff(
    conversation_id: UUID,
    payload: HandoffRequestPayload,
    context: TenantContextDependency,
    request: Request,
    db: DbSession,
) -> HandoffResponse:
    require_tenant_role(context, OperatorRoles)
    await _load_conversation_contact_binding(db, context.tenant.id, conversation_id)
    try:
        handoff = await request_handoff(
            get_session_factory(request),
            tenant_id=context.tenant.id,
            conversation_id=conversation_id,
            request_source=HandoffRequestSource.HUMAN,
            reason_code=payload.reason_code,
            reason_text=payload.reason_text,
            requested_by_user_id=context.current.user.id,
        )
    except HandoffRuntimeError as exc:
        raise _runtime_http_error(exc) from exc
    return _handoff_response(handoff)


@router.put("/{conversation_id}/claim", response_model=HandoffResponse)
async def claim_operator_conversation(
    conversation_id: UUID,
    context: TenantContextDependency,
    request: Request,
) -> HandoffResponse:
    require_tenant_role(context, OperatorRoles)
    try:
        handoff = await claim_handoff(
            get_session_factory(request),
            tenant_id=context.tenant.id,
            conversation_id=conversation_id,
            actor_user_id=context.current.user.id,
        )
    except HandoffRuntimeError as exc:
        raise _runtime_http_error(exc) from exc
    return _handoff_response(handoff)


@router.put("/{conversation_id}/release", response_model=HandoffResponse)
async def release_operator_conversation(
    conversation_id: UUID,
    context: TenantContextDependency,
    request: Request,
) -> HandoffResponse:
    role = require_tenant_role(context, OperatorRoles)
    try:
        handoff = await release_handoff(
            get_session_factory(request),
            tenant_id=context.tenant.id,
            conversation_id=conversation_id,
            actor_user_id=context.current.user.id,
            allow_override=_can_override(role),
        )
    except HandoffRuntimeError as exc:
        raise _runtime_http_error(exc) from exc
    return _handoff_response(handoff)


@router.put("/{conversation_id}/resolve", response_model=HandoffResponse)
async def resolve_operator_conversation(
    conversation_id: UUID,
    payload: ResolvePayload,
    context: TenantContextDependency,
    request: Request,
) -> HandoffResponse:
    role = require_tenant_role(context, OperatorRoles)
    try:
        resolution = await resolve_handoff(
            get_session_factory(request),
            tenant_id=context.tenant.id,
            conversation_id=conversation_id,
            actor_user_id=context.current.user.id,
            allow_override=_can_override(role),
            resume_ai=payload.resume_ai,
            cancel=payload.cancel,
        )
    except HandoffRuntimeError as exc:
        raise _runtime_http_error(exc) from exc
    if resolution.event_id_to_resume is not None:
        try:
            await get_channel_queue(request).enqueue(resolution.event_id_to_resume)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Handoff resolved but AI resume queue is unavailable; retry resume",
            ) from exc
    async with get_session_factory(request)() as db:
        handoff = await db.scalar(
            select(ConversationHandoff).where(
                ConversationHandoff.id == resolution.handoff_id,
                ConversationHandoff.tenant_id == context.tenant.id,
            )
        )
        if handoff is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Handoff not found")
        return _handoff_response(handoff)


@router.post("/{conversation_id}/reply", response_model=HumanReplyResponse)
async def send_operator_reply(
    conversation_id: UUID,
    payload: HumanReplyPayload,
    context: TenantContextDependency,
    request: Request,
    db: DbSession,
    settings: SettingsDependency,
) -> HumanReplyResponse:
    role = require_tenant_role(context, OperatorRoles)
    conversation, _, binding, _ = await _load_conversation_contact_binding(
        db, context.tenant.id, conversation_id
    )
    handoff = await _load_active_handoff_api(db, context.tenant.id, conversation_id)
    _ensure_reply_owner(handoff, actor_user_id=context.current.user.id, role=role)
    if binding is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conversation has no originating channel binding",
        )
    try:
        message = await dispatch_text(
            db,
            get_channel_registry(request),
            settings,
            tenant_id=context.tenant.id,
            channel_account_id=binding.channel_account_id,
            conversation_id=conversation.id,
            text=payload.text,
            idempotency_key=f"human:{handoff.id}:{payload.client_message_id}",
            author_type=MessageAuthorType.HUMAN,
        )
    except ChannelRuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.code) from exc
    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="handoff.operator_reply",
            target_type="message",
            target_id=message.id,
            details={
                "conversation_id": str(conversation.id),
                "handoff_id": str(handoff.id),
            },
        )
    )
    await db.commit()
    return HumanReplyResponse(
        message_id=message.id, text=message.text or "", occurred_at=message.occurred_at
    )


@router.post("/{conversation_id}/assist", response_model=AssistSuggestionResponse, status_code=201)
async def generate_operator_assist(
    conversation_id: UUID,
    context: TenantContextDependency,
    request: Request,
    db: DbSession,
) -> AssistSuggestionResponse:
    role = require_tenant_role(context, OperatorRoles)
    conversation, _, binding, account = await _load_conversation_contact_binding(
        db, context.tenant.id, conversation_id
    )
    handoff = await _load_active_handoff_api(db, context.tenant.id, conversation_id)
    _ensure_reply_owner(handoff, actor_user_id=context.current.user.id, role=role)
    if binding is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Conversation has no channel binding"
        )
    assignment = await db.scalar(
        select(AgentChannelAssignment).where(
            AgentChannelAssignment.tenant_id == context.tenant.id,
            AgentChannelAssignment.channel_account_id == binding.channel_account_id,
        )
    )
    if assignment is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="No agent assigned to channel"
        )
    agent = await db.scalar(
        select(Agent).where(
            Agent.id == assignment.agent_id,
            Agent.tenant_id == context.tenant.id,
            Agent.is_active.is_(True),
        )
    )
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Assigned agent unavailable"
        )
    prompt = await db.scalar(
        select(AgentPromptVersion).where(
            AgentPromptVersion.tenant_id == context.tenant.id,
            AgentPromptVersion.prompt_id == agent.prompt_id,
            AgentPromptVersion.status == PromptVersionStatus.PUBLISHED.value,
        )
    )
    if prompt is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Agent prompt is not published"
        )
    current = await db.scalar(
        select(Message)
        .where(
            Message.tenant_id == context.tenant.id,
            Message.conversation_id == conversation.id,
            Message.direction == MessageDirection.INBOUND.value,
            Message.author_type == MessageAuthorType.CUSTOMER.value,
            Message.text.is_not(None),
        )
        .order_by(Message.occurred_at.desc(), Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    if current is None or current.text is None or not current.text.strip():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="No customer text available"
        )
    model_input = await build_conversation_input(
        get_session_factory(request),
        tenant_id=context.tenant.id,
        conversation_id=conversation.id,
        current_message_id=current.id,
        current_text=current.text,
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Channel account unavailable"
        )
    channel_type = ChannelType(account.channel_type)
    instructions = (
        compose_instructions(prompt.content, channel_type) + "\n\n[OPERATOR ASSIST MODE]\n"
        "Draft one suggested customer-facing reply for the human operator. "
        "Do not claim the draft was sent or that an external action occurred."
    )
    try:
        result = await get_ai_gateway(request).generate(
            context.tenant.id,
            AITaskType.CUSTOMER_RESPONSE,
            GenerationRequest(input_text=model_input, instructions=instructions),
        )
    except AIRoutingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE
            if exc.retryable
            else status.HTTP_409_CONFLICT,
            detail=exc.code,
        ) from exc
    text = validate_customer_response(result.text)
    suggestion = OperatorAssistSuggestion(
        tenant_id=context.tenant.id,
        conversation_id=conversation.id,
        handoff_id=handoff.id,
        requested_by_user_id=context.current.user.id,
        text=text,
    )
    db.add(suggestion)
    await db.flush()
    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="handoff.assist.generated",
            target_type="operator_assist_suggestion",
            target_id=suggestion.id,
            details={
                "conversation_id": str(conversation.id),
                "handoff_id": str(handoff.id),
            },
        )
    )
    await db.commit()
    await db.refresh(suggestion)
    return AssistSuggestionResponse(
        id=suggestion.id, text=suggestion.text, created_at=suggestion.created_at
    )
