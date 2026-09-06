# Milestone 3 — Contacts, Identities, Conversations & Messages

## Goal

Create the canonical tenant-scoped customer interaction domain before AI providers and concrete channels are introduced.

The acceptance slice must prove that a tenant can persist and retrieve contacts, external identities, conversations, messages, and attachment metadata safely on PostgreSQL, with deterministic idempotency and cross-tenant isolation.

## Architecture

ADR-009 is authoritative for contact identity resolution and message idempotency.

Use the existing:

- FastAPI application and authenticated tenant context.
- SQLAlchemy 2.x persistence.
- Alembic migrations.
- PostgreSQL source of truth.
- Fixed tenant RBAC foundation from Milestone 2.

Do not add a channel SDK, AI SDK, queue framework, object-storage backend, generic repository framework, event bus abstraction, or contact-merge engine in this slice.

## Data model

Implement only these new persisted concepts.

### Contact

- UUID primary key.
- Tenant ID.
- Display name nullable.
- Active status.
- Created/updated timestamps.

A contact is independent from channel/provider identity.

### ExternalIdentity

- UUID primary key.
- Tenant ID.
- Contact ID.
- Namespace.
- External ID.
- Display name nullable.
- Created/updated timestamps.
- Unique `(tenant_id, namespace, external_id)`.

An identity belongs to one contact. Do not automatically merge existing contacts.

### Conversation

- UUID primary key.
- Tenant ID.
- Contact ID.
- Status: `open`, `resolved`, `closed`.
- Subject nullable.
- Created/updated timestamps.
- Last message timestamp nullable.

Human-handoff state is deferred to the dedicated handoff milestone; this status is only the basic conversation lifecycle foundation.

### Message

- UUID primary key.
- Tenant ID.
- Conversation ID.
- External identity ID nullable.
- Direction: `inbound`, `outbound`, `internal`.
- Author type: `customer`, `human`, `ai`, `system`.
- Message type: `text`, `voice`, `audio`, `image`, `video`, `document`, `location`, `other`.
- Text nullable.
- External message ID nullable.
- Idempotency key nullable.
- Safe external metadata JSON object.
- Occurred timestamp.
- Created timestamp.
- Unique `(tenant_id, idempotency_key)` when idempotency key is present.

For this milestone, non-text message records are allowed even when binary media is not yet stored.

### MessageAttachment

- UUID primary key.
- Tenant ID.
- Message ID.
- Media type.
- MIME type nullable.
- Filename nullable.
- Size bytes nullable.
- External media ID nullable.
- Storage key nullable.
- Safe metadata JSON object.
- Created timestamp.

No binary upload or object-storage selection is introduced here.

## Tenant and relationship invariants

- Every new row is explicitly tenant-owned.
- Tenant-scoped queries must include tenant filters; frontend filtering is never authorization.
- A contact referenced by a conversation must belong to the same tenant.
- An external identity must link to a contact in the same tenant.
- A message must link to a conversation in the same tenant.
- A message external identity, when present, must belong to the same tenant and contact as the conversation.
- An attachment must link to a message in the same tenant.

Where ordinary foreign keys cannot enforce composite tenant ownership alone, application checks and PostgreSQL-backed tests must prove the invariant. Prefer clear explicit checks over introducing a generic ORM repository layer.

## RBAC

For Admin Console APIs in this slice:

Read access:

- `owner`
- `admin`
- `supervisor`
- `agent`
- `analyst`
- `viewer`

Write access:

- `owner`
- `admin`
- `supervisor`
- `agent`

`analyst` and `viewer` are read-only.

This API RBAC does not define future internal channel-ingestion authorization; channel adapters/workers will use trusted application services in later milestones.

## API scope

Implement the smallest API needed to prove the domain:

```text
POST /api/v1/tenants/{tenant_id}/contacts
GET  /api/v1/tenants/{tenant_id}/contacts
GET  /api/v1/tenants/{tenant_id}/contacts/{contact_id}
POST /api/v1/tenants/{tenant_id}/contacts/{contact_id}/identities

POST /api/v1/tenants/{tenant_id}/conversations
GET  /api/v1/tenants/{tenant_id}/conversations
GET  /api/v1/tenants/{tenant_id}/conversations/{conversation_id}

POST /api/v1/tenants/{tenant_id}/conversations/{conversation_id}/messages
GET  /api/v1/tenants/{tenant_id}/conversations/{conversation_id}/messages
```

Responses may embed identity and attachment summaries where that keeps the API small and avoids additional premature endpoints.

## Message idempotency semantics

When `idempotency_key` is absent, a new message is created normally.

When it is present:

1. Look up `(tenant_id, idempotency_key)` before insertion.
2. If no message exists, create it transactionally.
3. If an existing message belongs to the same conversation and has the same immutable message identity fields, return the existing message instead of creating a duplicate.
4. If the key already belongs to a conflicting message/conversation, return `409 Conflict`.
5. PostgreSQL uniqueness is the final race-condition boundary; application logic must handle uniqueness conflicts safely.

Immutable identity fields for comparison in this milestone:

- conversation ID
- external identity ID
- direction
- author type
- message type
- external message ID

Text or metadata changes under an already-consumed idempotency key are not treated as an update operation.

## Identity resolution semantics

- Exact `(tenant_id, namespace, external_id)` lookup is authoritative.
- Adding an already-existing identity to the same contact is idempotent and returns that identity.
- Attempting to attach an existing identity to a different contact returns `409 Conflict`.
- No fuzzy matching or automatic contact merge.

## Validation

Input limits should remain bounded and practical:

- display name <= 200 chars.
- namespace <= 100 chars.
- external ID <= 255 chars.
- subject <= 300 chars.
- message text <= 100,000 chars.
- idempotency key <= 255 chars.
- external message ID <= 255 chars.
- attachment filename <= 500 chars.
- MIME type <= 255 chars.
- external media ID/storage key <= 1024 chars.
- attachment size must be non-negative.

JSON metadata must be an object. No raw credentials, access tokens, or full unbounded webhook payloads should be stored by these APIs.

## Tests

Required PostgreSQL-backed coverage:

- Contact creation/list/detail is tenant-scoped.
- Cross-tenant contact access is denied without data leakage.
- Exact external identity can be created and resolved safely.
- Same identity on the same contact is idempotent.
- Same identity cannot be attached to another contact.
- Conversation creation validates contact tenant ownership.
- Conversation listing/detail is tenant-scoped.
- Viewer/analyst cannot create customer-domain records.
- Owner/admin/supervisor/agent write path is accepted as applicable.
- Message creation validates conversation tenant ownership.
- Message external identity must match conversation contact/tenant.
- Duplicate idempotency key for the same immutable message returns the existing message.
- Conflicting reuse of an idempotency key returns 409.
- Same idempotency key may be used independently by different tenants.
- Attachment metadata persists and is returned with the message.
- Message listing is ordered deterministically by occurrence/creation/ID.
- Migration upgrade and Alembic drift check pass on PostgreSQL.

No SQLite substitute is acceptable for the acceptance gate.

## Explicitly out of scope

- Contact merge.
- Contact search/ranking beyond basic list ordering.
- CRM-grade profile fields.
- Channel/channel-account entities.
- Telegram/Website webhook payload parsing.
- AI-generated messages.
- Queue processing.
- Human assignment/handoff state machine.
- Binary media upload/download.
- Object-storage implementation.
- Message edit/delete/redaction policy.
- Full-text search.
- Pagination framework beyond a small bounded list limit if not immediately required.

## Acceptance gate

The slice is acceptable only when:

```text
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
alembic upgrade head
alembic check
```

and PostgreSQL-backed integration tests prove the tenant, identity, relationship, RBAC, and idempotency invariants above.

Docker/Compose regression must confirm the existing five-service runtime remains healthy after the migration is applied.

No real secrets or temporary validation workflows may remain in the final diff.
