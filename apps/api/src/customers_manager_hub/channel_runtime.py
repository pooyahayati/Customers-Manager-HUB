from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.channel_gateway import (
    CanonicalInboundMessage,
    ChannelProviderError,
    ChannelRegistry,
)
from customers_manager_hub.channel_models import (
    ChannelAccount,
    ChannelAccountCapability,
    ChannelCapability,
    ChannelCredential,
    ChannelCredentialKind,
    ChannelInboundEvent,
    ChannelInboundEventStatus,
    ChannelType,
    ConversationChannelBinding,
)
from customers_manager_hub.channel_security import decrypt_channel_secret
from customers_manager_hub.config import Settings
from customers_manager_hub.database import AsyncSessionFactory
from customers_manager_hub.models import (
    Contact,
    Conversation,
    ConversationStatus,
    ExternalIdentity,
    Message,
    MessageAttachment,
    MessageAuthorType,
    MessageDirection,
    MessageType,
)


class ChannelRuntimeError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class PersistedInboundEvent:
    event_id: UUID
    should_enqueue: bool
    ignored: bool


async def load_channel_account(
    db: AsyncSession,
    account_id: UUID,
    *,
    tenant_id: UUID | None = None,
) -> ChannelAccount | None:
    statement = select(ChannelAccount).where(ChannelAccount.id == account_id)
    if tenant_id is not None:
        statement = statement.where(ChannelAccount.tenant_id == tenant_id)
    return await db.scalar(statement)


async def load_channel_secret(
    db: AsyncSession,
    settings: Settings,
    account: ChannelAccount,
    kind: ChannelCredentialKind,
) -> str:
    credential = await db.scalar(
        select(ChannelCredential).where(
            ChannelCredential.tenant_id == account.tenant_id,
            ChannelCredential.channel_account_id == account.id,
            ChannelCredential.kind == kind.value,
        )
    )
    if credential is None:
        raise ChannelRuntimeError("channel_credential_missing")
    return decrypt_channel_secret(
        settings,
        account.tenant_id,
        account.id,
        kind,
        ciphertext=credential.ciphertext,
        nonce=credential.nonce,
        key_version=credential.key_version,
    )


async def _capability_enabled(
    db: AsyncSession,
    account: ChannelAccount,
    capability: ChannelCapability,
) -> bool:
    row = await db.scalar(
        select(ChannelAccountCapability).where(
            ChannelAccountCapability.tenant_id == account.tenant_id,
            ChannelAccountCapability.channel_account_id == account.id,
            ChannelAccountCapability.capability == capability.value,
            ChannelAccountCapability.enabled.is_(True),
        )
    )
    return row is not None


def _message_capability(message_type: MessageType) -> ChannelCapability | None:
    if message_type == MessageType.TEXT:
        return ChannelCapability.TEXT
    if message_type == MessageType.VOICE:
        return ChannelCapability.VOICE
    return None


async def persist_inbound_event(
    db: AsyncSession,
    account: ChannelAccount,
    *,
    external_event_id: str,
    canonical: CanonicalInboundMessage | None,
    ignored_reason: str | None,
) -> PersistedInboundEvent:
    for attempt in range(2):
        try:
            return await _persist_inbound_event_once(
                db,
                account,
                external_event_id=external_event_id,
                canonical=canonical,
                ignored_reason=ignored_reason,
            )
        except IntegrityError:
            await db.rollback()
            if attempt == 1:
                raise
    raise AssertionError("Unreachable channel ingestion retry state")


async def _persist_inbound_event_once(
    db: AsyncSession,
    account: ChannelAccount,
    *,
    external_event_id: str,
    canonical: CanonicalInboundMessage | None,
    ignored_reason: str | None,
) -> PersistedInboundEvent:
    existing_event = await db.scalar(
        select(ChannelInboundEvent).where(
            ChannelInboundEvent.channel_account_id == account.id,
            ChannelInboundEvent.external_event_id == external_event_id,
        )
    )
    if existing_event is not None:
        should_enqueue = existing_event.status == ChannelInboundEventStatus.RECEIVED.value
        return PersistedInboundEvent(
            event_id=existing_event.id,
            should_enqueue=should_enqueue,
            ignored=existing_event.status == ChannelInboundEventStatus.IGNORED.value,
        )

    if canonical is None:
        event = ChannelInboundEvent(
            tenant_id=account.tenant_id,
            channel_account_id=account.id,
            external_event_id=external_event_id,
            event_type="unsupported",
            status=ChannelInboundEventStatus.IGNORED.value,
            error_code=ignored_reason or "unsupported_update",
        )
        db.add(event)
        await db.commit()
        return PersistedInboundEvent(event_id=event.id, should_enqueue=False, ignored=True)

    capability = _message_capability(canonical.message_type)
    if capability is None or not await _capability_enabled(db, account, capability):
        event = ChannelInboundEvent(
            tenant_id=account.tenant_id,
            channel_account_id=account.id,
            external_event_id=external_event_id,
            event_type=canonical.message_type.value,
            status=ChannelInboundEventStatus.IGNORED.value,
            error_code="capability_disabled",
        )
        db.add(event)
        await db.commit()
        return PersistedInboundEvent(event_id=event.id, should_enqueue=False, ignored=True)

    identity = await db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.tenant_id == account.tenant_id,
            ExternalIdentity.namespace == canonical.sender_namespace,
            ExternalIdentity.external_id == canonical.sender_external_id,
        )
    )
    if identity is None:
        contact = Contact(
            tenant_id=account.tenant_id,
            display_name=canonical.sender_display_name,
        )
        db.add(contact)
        await db.flush()
        identity = ExternalIdentity(
            tenant_id=account.tenant_id,
            contact_id=contact.id,
            namespace=canonical.sender_namespace,
            external_id=canonical.sender_external_id,
            display_name=canonical.sender_display_name,
        )
        db.add(identity)
        await db.flush()
    else:
        contact = await db.scalar(
            select(Contact).where(
                Contact.id == identity.contact_id,
                Contact.tenant_id == account.tenant_id,
            )
        )
        if contact is None:
            raise ChannelRuntimeError("channel_identity_contact_missing")

    binding = await db.scalar(
        select(ConversationChannelBinding).where(
            ConversationChannelBinding.tenant_id == account.tenant_id,
            ConversationChannelBinding.channel_account_id == account.id,
            ConversationChannelBinding.external_thread_id == canonical.external_thread_id,
        )
    )
    if binding is None:
        conversation = Conversation(
            tenant_id=account.tenant_id,
            contact_id=contact.id,
            status=ConversationStatus.OPEN.value,
        )
        db.add(conversation)
        await db.flush()
        binding = ConversationChannelBinding(
            tenant_id=account.tenant_id,
            conversation_id=conversation.id,
            channel_account_id=account.id,
            external_thread_id=canonical.external_thread_id,
        )
        db.add(binding)
        await db.flush()
    else:
        conversation = await db.scalar(
            select(Conversation).where(
                Conversation.id == binding.conversation_id,
                Conversation.tenant_id == account.tenant_id,
            )
        )
        if conversation is None:
            raise ChannelRuntimeError("channel_binding_conversation_missing")
        if conversation.contact_id != contact.id:
            raise ChannelRuntimeError("channel_binding_identity_conflict")
        if conversation.status != ConversationStatus.OPEN.value:
            conversation.status = ConversationStatus.OPEN.value

    idempotency_key = (
        f"telegram:{account.id}:chat:{canonical.external_thread_id}:"
        f"message:{canonical.external_message_id}"
    )
    existing_message = await db.scalar(
        select(Message).where(
            Message.tenant_id == account.tenant_id,
            Message.idempotency_key == idempotency_key,
        )
    )
    if existing_message is None:
        message = Message(
            tenant_id=account.tenant_id,
            conversation_id=conversation.id,
            external_identity_id=identity.id,
            direction=MessageDirection.INBOUND.value,
            author_type=MessageAuthorType.CUSTOMER.value,
            message_type=canonical.message_type.value,
            text=canonical.text,
            external_message_id=canonical.external_message_id,
            idempotency_key=idempotency_key,
            external_metadata=canonical.metadata,
            occurred_at=canonical.occurred_at,
        )
        db.add(message)
        await db.flush()
        for attachment in canonical.attachments:
            db.add(
                MessageAttachment(
                    tenant_id=account.tenant_id,
                    message_id=message.id,
                    media_type=attachment.media_type.value,
                    mime_type=attachment.mime_type,
                    filename=attachment.filename,
                    size_bytes=attachment.size_bytes,
                    external_media_id=attachment.external_media_id,
                    media_metadata=attachment.metadata,
                )
            )
    else:
        if existing_message.conversation_id != conversation.id:
            raise ChannelRuntimeError("channel_message_idempotency_conflict")
        message = existing_message

    if conversation.last_message_at is None or canonical.occurred_at > conversation.last_message_at:
        conversation.last_message_at = canonical.occurred_at

    event = ChannelInboundEvent(
        tenant_id=account.tenant_id,
        channel_account_id=account.id,
        external_event_id=external_event_id,
        event_type=canonical.message_type.value,
        status=ChannelInboundEventStatus.RECEIVED.value,
        message_id=message.id,
    )
    db.add(event)
    await db.commit()
    return PersistedInboundEvent(event_id=event.id, should_enqueue=True, ignored=False)


async def mark_event_enqueued(db: AsyncSession, event_id: UUID) -> None:
    event = await db.scalar(select(ChannelInboundEvent).where(ChannelInboundEvent.id == event_id))
    if event is None:
        raise ChannelRuntimeError("channel_event_missing")
    if event.status == ChannelInboundEventStatus.RECEIVED.value:
        event.status = ChannelInboundEventStatus.ENQUEUED.value
        await db.commit()


async def mark_event_ignored(
    session_factory: AsyncSessionFactory,
    event_id: UUID,
    error_code: str,
) -> None:
    async with session_factory() as db:
        event = await db.scalar(select(ChannelInboundEvent).where(ChannelInboundEvent.id == event_id))
        if event is None or event.status == ChannelInboundEventStatus.PROCESSED.value:
            return
        event.status = ChannelInboundEventStatus.IGNORED.value
        event.error_code = error_code[:100]
        await db.commit()


async def process_channel_event(
    session_factory: AsyncSessionFactory,
    registry: ChannelRegistry,
    settings: Settings,
    event_id: UUID,
) -> None:
    async with session_factory() as db:
        event = await db.scalar(select(ChannelInboundEvent).where(ChannelInboundEvent.id == event_id))
        if event is None:
            raise ChannelRuntimeError("channel_event_missing")
        if event.status in {
            ChannelInboundEventStatus.PROCESSED.value,
            ChannelInboundEventStatus.IGNORED.value,
        }:
            return

        account = await load_channel_account(db, event.channel_account_id, tenant_id=event.tenant_id)
        if account is None:
            raise ChannelRuntimeError("channel_account_missing")
        if not account.is_active:
            event.status = ChannelInboundEventStatus.IGNORED.value
            event.error_code = "channel_inactive"
            await db.commit()
            return
        if event.message_id is None:
            event.status = ChannelInboundEventStatus.IGNORED.value
            event.error_code = "channel_event_message_missing"
            await db.commit()
            return

        message = await db.scalar(
            select(Message).where(
                Message.id == event.message_id,
                Message.tenant_id == event.tenant_id,
            )
        )
        if message is None:
            raise ChannelRuntimeError("channel_message_missing")

        if message.message_type == MessageType.VOICE.value:
            attachment = await db.scalar(
                select(MessageAttachment).where(
                    MessageAttachment.tenant_id == event.tenant_id,
                    MessageAttachment.message_id == message.id,
                    MessageAttachment.media_type == MessageType.VOICE.value,
                )
            )
            if attachment is None or attachment.external_media_id is None:
                raise ChannelRuntimeError("channel_voice_attachment_missing")
            if account.channel_type != ChannelType.TELEGRAM.value:
                raise ChannelRuntimeError("channel_voice_adapter_unsupported")
            access_secret = await load_channel_secret(
                db,
                settings,
                account,
                ChannelCredentialKind.TELEGRAM_BOT_TOKEN,
            )
            adapter = registry.get(ChannelType.TELEGRAM)
            media = await adapter.resolve_media(access_secret, attachment.external_media_id)
            expected_unique_id = attachment.media_metadata.get("telegram_file_unique_id")
            if expected_unique_id is not None and expected_unique_id != media.external_unique_id:
                raise ChannelProviderError("telegram_file_identity_mismatch", retryable=False)
            metadata = dict(attachment.media_metadata)
            metadata["telegram_file_unique_id"] = media.external_unique_id
            if media.file_path is not None:
                metadata["telegram_file_path"] = media.file_path
            attachment.media_metadata = metadata
            if media.size_bytes is not None:
                attachment.size_bytes = media.size_bytes

        event.status = ChannelInboundEventStatus.PROCESSED.value
        event.error_code = None
        await db.commit()


async def dispatch_text(
    db: AsyncSession,
    registry: ChannelRegistry,
    settings: Settings,
    *,
    tenant_id: UUID,
    channel_account_id: UUID,
    conversation_id: UUID,
    text: str,
    idempotency_key: str,
    author_type: MessageAuthorType,
) -> Message:
    existing = await db.scalar(
        select(Message).where(
            Message.tenant_id == tenant_id,
            Message.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if (
            existing.conversation_id != conversation_id
            or existing.direction != MessageDirection.OUTBOUND.value
            or existing.message_type != MessageType.TEXT.value
        ):
            raise ChannelRuntimeError("channel_outbound_idempotency_conflict")
        return existing

    account = await load_channel_account(db, channel_account_id, tenant_id=tenant_id)
    if account is None or not account.is_active:
        raise ChannelRuntimeError("channel_account_unavailable")
    binding = await db.scalar(
        select(ConversationChannelBinding).where(
            ConversationChannelBinding.tenant_id == tenant_id,
            ConversationChannelBinding.channel_account_id == account.id,
            ConversationChannelBinding.conversation_id == conversation_id,
        )
    )
    if binding is None:
        raise ChannelRuntimeError("channel_conversation_binding_missing")
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == tenant_id,
        )
    )
    if conversation is None:
        raise ChannelRuntimeError("channel_conversation_missing")
    if account.channel_type != ChannelType.TELEGRAM.value:
        raise ChannelRuntimeError("channel_text_adapter_unsupported")

    access_secret = await load_channel_secret(
        db,
        settings,
        account,
        ChannelCredentialKind.TELEGRAM_BOT_TOKEN,
    )
    adapter = registry.get(ChannelType.TELEGRAM)
    sent = await adapter.send_text(
        access_secret,
        external_thread_id=binding.external_thread_id,
        text=text,
    )
    identity = await db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.tenant_id == tenant_id,
            ExternalIdentity.contact_id == conversation.contact_id,
            ExternalIdentity.namespace == "telegram:user",
            ExternalIdentity.external_id == binding.external_thread_id,
        )
    )
    message = Message(
        tenant_id=tenant_id,
        conversation_id=conversation.id,
        external_identity_id=identity.id if identity is not None else None,
        direction=MessageDirection.OUTBOUND.value,
        author_type=author_type.value,
        message_type=MessageType.TEXT.value,
        text=text.strip(),
        external_message_id=sent.external_message_id,
        idempotency_key=idempotency_key,
        external_metadata={
            "channel_type": account.channel_type,
            "channel_account_id": str(account.id),
            "external_thread_id": binding.external_thread_id,
        },
        occurred_at=sent.occurred_at,
    )
    db.add(message)
    if conversation.last_message_at is None or sent.occurred_at > conversation.last_message_at:
        conversation.last_message_at = sent.occurred_at
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raced = await db.scalar(
            select(Message).where(
                Message.tenant_id == tenant_id,
                Message.idempotency_key == idempotency_key,
            )
        )
        if raced is None:
            raise
        return raced
    await db.refresh(message)
    return message
