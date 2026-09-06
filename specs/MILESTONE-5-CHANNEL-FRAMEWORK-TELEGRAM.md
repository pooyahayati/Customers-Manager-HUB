# Milestone 5 Specification — Channel Framework & Telegram

## Status

Implementation specification for Roadmap Milestone 5.

## Goal

Prove the canonical channel architecture with one real external channel without introducing Telegram-specific logic into the conversation or AI core.

## In Scope

### Channel domain

- Generic `ChannelAccount` tenant-owned entity.
- Generic tenant-owned `ChannelCredential` encrypted-secret boundary.
- Generic `ConversationChannelBinding` mapping a canonical conversation to an external channel thread/chat.
- Minimal `ChannelInboundEvent` state for webhook deduplication, queue correlation, and processing status.
- Connector capability registry.
- Tenant-controlled enabled inbound capabilities.
- Canonical inbound message contract used by connector/core integration.
- Channel adapter protocol and registry.

### Initial Telegram capabilities

Connector-supported capabilities in this milestone:

- inbound text;
- inbound voice;
- outbound text.

Tenant configuration may enable/disable inbound text and inbound voice independently. Outbound text is a connector capability and is not exposed as an inbound toggle.

Only Telegram private chats with a concrete sender user are ingested in this first slice. Group, supergroup, channel, edited-message, callback-query, and business-account update semantics are deferred.

### Telegram account configuration

Authenticated Owner/Admin APIs can:

- register a Telegram bot account with a bot token;
- validate the bot token with Telegram `getMe`;
- store the token encrypted;
- generate/store a webhook secret encrypted;
- configure enabled inbound capabilities;
- list/read channel metadata without exposing secrets;
- activate/deactivate the channel account;
- register the Telegram webhook through `setWebhook` when `TELEGRAM_WEBHOOK_BASE_URL` is configured.

Webhook registration uses only the `message` update type for this milestone.

### Public Telegram webhook

Endpoint shape:

`POST /api/v1/webhooks/telegram/{channel_account_id}`

Requirements:

1. Load the active Telegram channel account.
2. Decrypt the stored webhook secret.
3. Compare `X-Telegram-Bot-Api-Secret-Token` using constant-time comparison.
4. Validate the Telegram update shape.
5. Accept only private-chat `message` updates with text or voice.
6. Enforce the tenant-enabled inbound capability.
7. Deduplicate the Telegram `update_id` per channel account.
8. Normalize the sender into external identity namespace `telegram:user`.
9. Resolve/create a Contact.
10. Resolve/create a canonical Conversation through `ConversationChannelBinding` keyed by channel account + Telegram chat ID.
11. Persist an inbound canonical Message.
12. Use message idempotency key `telegram:{channel_account_id}:chat:{chat_id}:message:{message_id}`.
13. Persist a `ChannelInboundEvent` linked to the canonical message.
14. Commit PostgreSQL state before queue publication.
15. Enqueue only durable IDs to the Redis Stream.
16. Return a successful acknowledgement promptly.

If durable persistence succeeded but Redis publication fails, return a retryable non-2xx response. A repeated Telegram update must resolve the existing event/message and enqueue the same durable event again rather than create duplicate canonical state.

Unsupported/disabled message capabilities are acknowledged without creating a canonical message and are recorded as ignored inbound events when enough identifiers are available.

### Telegram voice handling

On webhook ingestion, persist voice metadata available directly from Telegram, including:

- `file_id` as the external media ID;
- `file_unique_id`;
- duration;
- MIME type when supplied;
- file size when supplied.

The channel worker resolves Telegram file metadata through `getFile` and stores the returned `file_path` as non-secret media metadata. Binary media persistence is explicitly deferred to Milestone 10, where the media/object-storage lifecycle and transcription pipeline are implemented.

Telegram's hosted Bot API currently limits bot file downloads to 20 MiB; Milestone 5 does not attempt to bypass this by introducing a local Bot API server.

### Queue and worker

Use ADR-010 Redis Streams semantics:

- versioned stream key;
- one consumer group;
- `XREADGROUP` for new work;
- `XACK` after success;
- delete acknowledged completed entries;
- periodic `XAUTOCLAIM` of idle pending entries;
- PostgreSQL is the source of truth;
- queue entries contain only event/message/account identifiers and job type;
- worker processing is idempotent.

Milestone 5 worker responsibility is intentionally small: enrich voice attachment metadata through Telegram `getFile`, then mark the inbound event processed. Text events can be marked processed without AI behavior. Agent orchestration begins in Milestone 6.

### Outbound text test dispatch

Provide an authenticated test/operational endpoint for Owner/Admin/Supervisor/Agent roles that:

- accepts a tenant-scoped canonical conversation ID, text, and caller-provided idempotency key;
- resolves its `ConversationChannelBinding` and active channel account;
- dispatches through the generic channel adapter;
- for Telegram, calls `sendMessage` with the bound chat ID;
- persists the canonical outbound Message with Telegram's returned message ID and timestamp after successful delivery;
- never exposes the bot token.

This endpoint exists to prove outbound transport before Agent Runtime. Automated AI response orchestration is out of scope.

## Canonical Channel Contract

The connector-normalized inbound contract must include at minimum:

- channel type;
- channel account ID;
- external event ID;
- external message ID;
- external thread/chat ID;
- sender namespace and sender external ID;
- optional sender display name;
- canonical message type;
- optional text;
- occurrence timestamp in UTC;
- attachment/media metadata;
- safe external metadata.

Raw Telegram payloads are not persisted as canonical message metadata.

## Security Requirements

- Telegram bot tokens and webhook secrets are encrypted at rest according to ADR-011.
- `ENCRYPTION_KEY` is deployment-level configuration and never committed.
- Bot tokens/webhook secrets/ciphertext/nonces are never returned from Admin APIs.
- Webhook secret comparison is constant-time.
- Telegram API errors are normalized; arbitrary provider response bodies are not persisted.
- Telegram bot token is never placed in queue data, message metadata, audit details, or logs.
- Tenant-scoped Admin channel routes use existing server-side tenant membership checks.
- Cross-tenant channel account/conversation/binding access returns non-leaking 404 semantics where applicable.
- Channel account mutations append non-sensitive audit events.
- Production/staging webhook base URL must be HTTPS.

## Data Model

New tables:

- `channel_accounts`
- `channel_credentials`
- `channel_account_capabilities`
- `conversation_channel_bindings`
- `channel_inbound_events`

No Telegram-specific table is required. Telegram-specific transport metadata stays behind the adapter and in safe generic metadata fields.

## API Surface

Tenant Admin API:

- `POST /api/v1/tenants/{tenant_id}/channels/telegram`
- `GET /api/v1/tenants/{tenant_id}/channels`
- `GET /api/v1/tenants/{tenant_id}/channels/{channel_account_id}`
- `PATCH /api/v1/tenants/{tenant_id}/channels/{channel_account_id}`
- `POST /api/v1/tenants/{tenant_id}/channels/{channel_account_id}/register-webhook`
- `POST /api/v1/tenants/{tenant_id}/channels/{channel_account_id}/send-text`

Public connector API:

- `POST /api/v1/webhooks/telegram/{channel_account_id}`

## Explicitly Out of Scope

- Agent/prompt orchestration.
- AI response generation from Telegram messages.
- Telegram group/supergroup/channel semantics.
- Edited messages, callbacks, inline queries, reactions, business connections, Rich Messages.
- Telegram media binary/object-storage persistence.
- Voice transcription.
- Outbound voice/media.
- Website Chat.
- Dead-letter UI or complete production queue operations.
- Generic external secret-manager integration and automatic key rotation.

## Acceptance Tests

Automated tests must cover at least:

1. Channel credential AES-GCM round-trip and authentication failure on wrong AAD/key.
2. Secrets absent from API response/audit/queue payloads.
3. Telegram `getMe`, `setWebhook`, `getFile`, and `sendMessage` request/response contracts with `httpx2.MockTransport`.
4. Valid webhook secret accepted; missing/incorrect secret rejected.
5. Text Telegram update creates one Contact, one ExternalIdentity, one Conversation/binding, one Message, one inbound event, and one queue job.
6. Duplicate update/message does not duplicate canonical state and can be safely re-enqueued.
7. Cross-tenant channel access blocked.
8. Disabled capability prevents canonical message ingestion.
9. Voice update stores canonical attachment metadata; worker `getFile` enrichment updates safe file-path metadata and marks the event processed.
10. Queue consumer group acknowledges successful work and reclaims idle pending work.
11. Outbound text dispatch calls `sendMessage` through the adapter and persists a canonical outbound Message.
12. Owner/Admin can configure channels; read-only roles cannot mutate; Supervisor/Agent can use the outbound test dispatch but cannot change channel credentials/configuration.
13. Migration applies from `0003` and `alembic check` reports no drift.
14. Full Docker Compose runtime still starts API, Web, Worker, PostgreSQL, and Redis with API/Worker non-root.

## Exit Gate

Telegram text and voice messages enter the canonical tenant-scoped conversation model, inbound jobs cross the Redis worker boundary, voice references are resolved with `getFile`, and outbound text can be dispatched through the channel adapter without Telegram-specific logic inside the conversation or AI core.
