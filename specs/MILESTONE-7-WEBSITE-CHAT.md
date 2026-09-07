# Milestone 7 Specification — Website Chat Channel

## Status

Implementation specification for Roadmap Milestone 7.

## Goal

Add a public Website Chat transport that reuses the canonical Contact, Conversation,
Message, Agent, Prompt, AI Gateway, worker, and outbound-message runtime already proven
by Telegram. A tenant must be able to configure a Website channel with exact allowed
origins, embed a lightweight browser widget, receive a customer text message, and obtain
the Agent response through the same `customer_response` task profile without duplicating
AI business logic.

## In Scope

### Website channel configuration

- Tenant-owned Website `ChannelAccount` using the existing channel domain.
- Owner/Admin configuration API.
- Exact allowed-origin list per Website channel account.
- Text receive and outbound-text capability declaration.
- Existing Agent-to-channel assignment is reused unchanged.
- Configuration mutation is audited without session tokens or customer message content.

### Public browser session

- Public session creation is addressed by opaque channel-account ID, never by tenant ID.
- Every public request validates the browser `Origin` against the configured exact-origin list.
- Session creation returns a cryptographically random opaque token once.
- Only a SHA-256 digest of the session token is persisted.
- Sessions expire after a bounded lifetime and are scoped to one tenant/channel account.
- A session owns an anonymous stable visitor UUID used by canonical identity resolution.
- The contract intentionally leaves a future extension point for a server-verified known-user
  identity assertion; browsers may not self-assert trusted CRM/customer identity in M7.

### Inbound transport

- Public Website messages are text-only in M7.
- Browser supplies a UUID `client_message_id` for retry-safe idempotency.
- Website payload is normalized into `CanonicalInboundMessage` before persistence.
- The existing `persist_inbound_event()` path resolves Contact/ExternalIdentity/Conversation,
  persists canonical Message, and enqueues the Redis Streams job.
- Web requests never execute AI synchronously.

### Outbound transport

- Website outbound delivery is canonical-message persistence, not an external provider API call.
- Agent Runtime continues to call generic `dispatch_text()`.
- For Website channels, `dispatch_text()` persists the outbound Message with the existing
  PostgreSQL advisory idempotency boundary and a synthetic Website external message ID.
- Browser clients retrieve canonical text messages through an authenticated polling endpoint.
- Initial polling avoids adding WebSocket/SSE connection infrastructure before it is required.

### Webchat artifact

- `apps/webchat` is a separate lightweight public-client artifact as required by ADR-007.
- It has no dependency on `apps/web`, Admin authentication, React, or Next.js.
- The initial widget is a dependency-free browser script using Shadow DOM.
- It accepts `data-api-base` and `data-channel-account-id` runtime configuration.
- It stores only Website session ID/token in browser local storage when available.
- It renders message content with `textContent`, not untrusted HTML.

## Data Model

Extend `channel_accounts.channel_type` to support `website` in addition to `telegram`.

New tables:

- `website_channel_origins`
  - tenant ID
  - channel-account ID
  - normalized exact origin
  - created timestamp
  - unique `(channel_account_id, origin)`
- `website_chat_sessions`
  - tenant ID
  - channel-account ID
  - anonymous visitor UUID
  - SHA-256 session-token digest
  - expiry timestamp
  - created/updated timestamps

No Website-specific Conversation, Message, Agent, Prompt, or AI tables are introduced.

## API Surface

Tenant Admin API:

- `POST /api/v1/tenants/{tenant_id}/channels/website`
- `GET /api/v1/tenants/{tenant_id}/channels/website`
- `GET /api/v1/tenants/{tenant_id}/channels/website/{channel_account_id}`
- `PUT /api/v1/tenants/{tenant_id}/channels/website/{channel_account_id}/origins`

Public Website API:

- `OPTIONS /api/v1/public/website/{channel_account_id}/{path...}`
- `POST /api/v1/public/website/{channel_account_id}/sessions`
- `POST /api/v1/public/website/{channel_account_id}/sessions/{session_id}/messages`
- `GET /api/v1/public/website/{channel_account_id}/sessions/{session_id}/messages`

Public message/session calls never accept a tenant ID as authorization input.

## Origin Policy

Allowed origins are normalized and stored as exact origins only:

- scheme must be `https`, except loopback/localhost development origins may use `http`;
- host is required;
- explicit port is preserved;
- userinfo, path (other than `/`), query, and fragment are rejected;
- wildcard origins are not supported in M7;
- `Origin: null` is rejected.

Every public endpoint, including CORS preflight, resolves the channel account first and checks
the request origin against its tenant-scoped origin rows.

## Session Security

- Session tokens use at least 256 bits of cryptographic randomness.
- Raw tokens are returned only at session creation and never persisted, logged, audited, placed
  in Redis jobs, or exposed by Admin APIs.
- Token verification compares the stored digest and presented-token digest using constant-time
  comparison.
- Sessions expire after 30 days in M7.
- Public message reads/writes require the token and exact allowed origin.
- Channel deactivation immediately blocks new and existing public Website requests.

## Canonical Identity and Conversation Mapping

For Website sessions:

- sender namespace: `website:visitor`
- sender external ID: session visitor UUID
- external thread ID: Website session UUID
- external message ID: browser-generated `client_message_id`

The generic channel runtime therefore creates/reuses canonical Contact, ExternalIdentity,
Conversation, ConversationChannelBinding, Message, and ChannelInboundEvent records.

## Agent Runtime

Website text events use the existing Agent assignment and durable AgentRun flow.

The only Agent-runtime Website-specific behavior is a deterministic channel instruction:

- plain text only;
- maximum 4096 characters for the initial widget transport.

Customer content remains model input, not a trusted instruction layer.

## Explicitly Out of Scope

- WebSocket/SSE streaming.
- Typing indicators.
- Attachments/media in Website Chat.
- Browser-side trusted known-customer assertions.
- Human handoff/operator inbox (M11).
- Per-tenant custom widget builds.
- Rich cards/buttons/reactions.
- Website voice.
- RAG, tools, memory, or policy changes.
- Global rate limiting/abuse controls beyond the exact-origin/session boundary; production-grade
  rate limiting remains part of later hardening and policy milestones.

## Acceptance Tests

Automated coverage must include at least:

1. Owner/Admin can configure Website channels/origins; lower roles cannot mutate them.
2. Cross-tenant Website channel reads return non-leaking 404 behavior.
3. Invalid/wildcard/non-HTTPS public origins are rejected except localhost development HTTP.
4. Public session creation succeeds only from an allowed origin and returns CORS headers.
5. Raw Website session token is not persisted or included in audit/Redis payloads.
6. Wrong session token, expired session, disallowed origin, inactive channel, and mismatched
   channel/session IDs are denied.
7. Retrying the same `client_message_id` does not duplicate canonical inbound Message/Event.
8. Website inbound text enters the existing Redis channel job queue.
9. Worker processing invokes the same mock `customer_response` task profile and Agent Runtime
   used by Telegram.
10. Website Agent response persists as one canonical outbound AI Message through
    `dispatch_text()` and AgentRun reaches `succeeded`.
11. Public message polling returns the canonical inbound/outbound text conversation without
    exposing internal metadata, prompt content, provider trace, tenant ID, or credentials.
12. Reprocessing/retrying a succeeded Website inbound event does not invoke AI again.
13. Telegram tests remain green after generalizing channel type support.
14. Migration applies from `0005`, `alembic check` reports no drift, and Docker runtime remains
    operational.

## Exit Gate

A tenant can configure a Website channel and allowed origin, assign an existing Agent, embed
the lightweight Webchat client, open a public browser session, send text, and receive one
AI-authored reply through the same canonical Conversation/Agent/AI Gateway/worker runtime used
by Telegram, with origin isolation and retry-safe persistence enforced server-side.
