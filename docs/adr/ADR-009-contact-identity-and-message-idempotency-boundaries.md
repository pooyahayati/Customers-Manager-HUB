# ADR-009 — Contact Identity and Message Idempotency Boundaries

## Status

Accepted

## Context

Milestone 3 introduces the canonical customer interaction domain before concrete channel implementations. The domain must preserve customer history across future channels without coupling contacts or conversations to Telegram, Website Chat, or another connector.

The same real-world customer may appear through multiple external identities, while external channel delivery may retry the same message/event. Incorrect automatic identity merging risks combining unrelated customers; missing idempotency risks duplicate history and later duplicate AI/business side effects.

## Decision

### Contact and external identity

A `Contact` is the tenant-owned customer record. An `ExternalIdentity` is a tenant-scoped identifier from an external namespace and links to exactly one contact.

- Contacts are channel-independent.
- External identities use a stable `namespace` plus `external_id` pair.
- `(tenant_id, namespace, external_id)` is unique.
- Namespace values are adapter-owned identifiers such as `telegram` or a future channel-account-scoped namespace.
- Identity lookup may resolve an existing contact from an exact trusted identity match.
- The platform does not automatically merge two existing contacts based on display name, email-like text, phone-like text, model inference, or other weak evidence.
- Contact merge is deferred until explicit merge policy and audit behavior are implemented.

### Conversation ownership

A conversation belongs to exactly one tenant and one contact. Conversation and message records retain `tenant_id` explicitly even when it can be inferred through foreign keys, so tenant filtering remains visible and enforceable in queries and future repository boundaries.

### Message idempotency

Messages may carry an optional canonical `idempotency_key`.

- `(tenant_id, idempotency_key)` is unique when the key is present.
- Channel adapters will later construct namespaced keys from authoritative external identifiers, for example channel/account/event identifiers.
- The conversation core does not interpret provider-specific key contents.
- Repeating a create-message request with the same tenant-scoped idempotency key returns the existing message when the immutable message identity matches, rather than inserting a duplicate.
- Reusing the same key for a conflicting conversation/message identity is rejected.
- Raw channel payloads are not required for idempotency and are not stored as the source of truth.

### Attachment metadata

Milestone 3 stores attachment/media metadata only. Binary object storage is not selected or introduced by this ADR. Storage references can be added when media ingestion is implemented.

## Alternatives Considered

### Put channel identifiers directly on Contact

Rejected because a contact may have multiple identities and channels, and it would couple the customer model to connector-specific fields.

### Automatically merge contacts by similar profile data

Rejected because false-positive merges are difficult to undo safely and violate the requirement for cautious identity resolution.

### Deduplicate only by external message ID

Rejected because external IDs may be scoped differently by provider/account. A canonical namespaced idempotency key keeps provider scoping at the adapter boundary.

### Use Redis as the authoritative deduplication store

Rejected. PostgreSQL is the durable source of truth for message history and must enforce uniqueness transactionally. Redis may later assist coordination but must not be the sole correctness boundary.

## Consequences

Positive:

- Contact history remains channel-independent.
- Tenant isolation is explicit at persistence/query boundaries.
- Channel retries can be made safe without provider-specific logic in the conversation core.
- Identity resolution is conservative and avoids unsafe implicit merges.
- Future connectors can choose correct provider/account namespaces without schema forks.

Trade-offs:

- Tenant ID is intentionally duplicated on several related tables and must be validated consistently.
- Contact merge is not available in this milestone.
- Adapter design must construct stable idempotency keys correctly when channels are implemented.

## Follow-up Implications

- Milestone 3 must prove cross-tenant negative cases for contacts, identities, conversations, messages, and idempotency keys using PostgreSQL.
- Milestone 5 channel adapters must create deterministic namespace/idempotency values from verified external payloads.
- A future contact-merge feature requires an explicit policy, audit trail, and conflict-handling design.
