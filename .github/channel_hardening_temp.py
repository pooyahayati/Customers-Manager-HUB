from pathlib import Path
from textwrap import dedent, indent


runtime = Path("apps/api/src/customers_manager_hub/channel_runtime.py")
content = runtime.read_text()
if not content.startswith("import hashlib\n"):
    content = "import hashlib\n" + content
content = content.replace(
    "from sqlalchemy import select\n",
    "from sqlalchemy import select, text as sql_text\n",
    1,
)
prefix, separator, _ = content.partition("async def dispatch_text(\n")
if not separator:
    raise SystemExit("dispatch_text function not found")
replacement = dedent(
    '''\
    def _outbound_lock_key(tenant_id: UUID, idempotency_key: str) -> int:
        digest = hashlib.sha256(f"{tenant_id}:{idempotency_key}".encode()).digest()
        value = int.from_bytes(digest[:8], "big", signed=False)
        return value if value < 2**63 else value - 2**64


    def _outbound_message_matches(
        message: Message,
        *,
        tenant_id: UUID,
        channel_account_id: UUID,
        conversation_id: UUID,
        normalized_text: str,
        author_type: MessageAuthorType,
    ) -> bool:
        return (
            message.tenant_id == tenant_id
            and message.conversation_id == conversation_id
            and message.direction == MessageDirection.OUTBOUND.value
            and message.author_type == author_type.value
            and message.message_type == MessageType.TEXT.value
            and message.text == normalized_text
            and message.external_metadata.get("channel_account_id") == str(channel_account_id)
        )


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
        normalized_text = text.strip()
        if not normalized_text:
            raise ChannelRuntimeError("channel_outbound_empty_text")

        await db.execute(
            sql_text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _outbound_lock_key(tenant_id, idempotency_key)},
        )
        existing = await db.scalar(
            select(Message).where(
                Message.tenant_id == tenant_id,
                Message.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if not _outbound_message_matches(
                existing,
                tenant_id=tenant_id,
                channel_account_id=channel_account_id,
                conversation_id=conversation_id,
                normalized_text=normalized_text,
                author_type=author_type,
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
            text=normalized_text,
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
            text=normalized_text,
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
            if not _outbound_message_matches(
                raced,
                tenant_id=tenant_id,
                channel_account_id=channel_account_id,
                conversation_id=conversation_id,
                normalized_text=normalized_text,
                author_type=author_type,
            ):
                raise ChannelRuntimeError("channel_outbound_idempotency_conflict")
            return raced
        await db.refresh(message)
        return message
    '''
)
runtime.write_text(prefix + replacement)


webhook = Path("apps/api/src/customers_manager_hub/channel_webhooks.py")
content = webhook.read_text()
old = dedent(
    '''\
    body = await request.body()
    if len(body) > _MAX_TELEGRAM_WEBHOOK_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
    try:
        payload: object = json.loads(body)
    '''
)
new = dedent(
    '''\
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_TELEGRAM_WEBHOOK_BYTES:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        body.extend(chunk)
    try:
        payload: object = json.loads(body)
    '''
)
if old not in content:
    raise SystemExit("Webhook body block not found")
webhook.write_text(content.replace(old, new, 1))


test = Path("apps/api/tests/test_telegram_channel_integration.py")
content = test.read_text()
content = content.replace(
    "from customers_manager_hub.channel_runtime import process_channel_event",
    "from customers_manager_hub.channel_runtime import dispatch_text, process_channel_event",
    1,
)
content = content.replace(
    "    MessageAttachment,\n    MessageDirection,",
    "    MessageAttachment,\n    MessageAuthorType,\n    MessageDirection,",
    1,
)
content = content.replace(
    "    ) -> ChannelSendResult:\n        self.send_calls.append((access_secret, external_thread_id, text))",
    "    ) -> ChannelSendResult:\n        await asyncio.sleep(0.05)\n        self.send_calls.append((access_secret, external_thread_id, text))",
    1,
)
marker = (
    '        assert repeated.status_code == 200\n'
    '        assert repeated.json()["id"] == sent.json()["id"]\n'
    '        assert len(adapter.send_calls) == 1\n'
)
addition = indent(
    dedent(
        '''\
        assert repeated.status_code == 200
        assert repeated.json()["id"] == sent.json()["id"]
        assert len(adapter.send_calls) == 1

        conflicting = supervisor_client.post(
            f"/api/v1/tenants/{tenant_id}/channels/{account_id}/send-text",
            json={**payload, "text": "Different reply"},
        )
        assert conflicting.status_code == 409
        assert len(adapter.send_calls) == 1

        async def send_concurrently() -> list[UUID]:
            engine, session_factory = create_database(TEST_SETTINGS)

            async def send_once() -> UUID:
                async with session_factory() as db:
                    message = await dispatch_text(
                        db,
                        ChannelRegistry((adapter,)),
                        TEST_SETTINGS,
                        tenant_id=tenant_id,
                        channel_account_id=account_id,
                        conversation_id=conversation_id,
                        text="Concurrent reply",
                        idempotency_key="telegram-outbound:concurrent",
                        author_type=MessageAuthorType.HUMAN,
                    )
                    return message.id

            try:
                return list(await asyncio.gather(send_once(), send_once()))
            finally:
                await engine.dispose()

        concurrent_ids = asyncio.run(send_concurrently())
        assert concurrent_ids[0] == concurrent_ids[1]
        assert len(adapter.send_calls) == 2
        '''
    ),
    "        ",
)
if marker not in content:
    raise SystemExit("Outbound test marker not found")
test.write_text(content.replace(marker, addition, 1))


spec = Path("specs/MILESTONE-5-CHANNEL-FRAMEWORK-TELEGRAM.md")
content = spec.read_text()
marker = "- never exposes the bot token.\n\nThis endpoint exists to prove outbound transport before Agent Runtime."
replacement = dedent(
    '''\
    - never exposes the bot token.
    - serializes concurrent requests that reuse the same tenant/idempotency key before calling the external provider;
    - returns an idempotency conflict when the same key is reused with a different outbound payload.

    Telegram does not expose an idempotency token for `sendMessage`. A process failure after Telegram accepts a message but before PostgreSQL commits can therefore still produce at-least-once delivery on a later retry. Durable outbound delivery/outbox reconciliation is deferred to the Agent/production-hardening lifecycle rather than falsely claiming exactly-once external delivery.

    This endpoint exists to prove outbound transport before Agent Runtime.'''
)
if marker not in content:
    raise SystemExit("Spec outbound marker not found")
spec.write_text(content.replace(marker, replacement, 1))
