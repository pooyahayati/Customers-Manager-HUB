# ADR-012 — Versioned Prompts and Durable Agent Runs

## Status

Accepted

## Context

Milestone 6 introduces tenant-configurable agents that can automatically answer canonical customer conversations through the provider-independent AI Gateway and the originating channel adapter.

The runtime needs stable prompt semantics, explicit channel-to-agent routing, and retry-safe orchestration. A Redis job may be redelivered, a worker may crash between AI generation and channel dispatch, and a tenant may publish a newer prompt while an older inbound message is still being processed.

The implementation must not move conversation truth into Redis, make provider SDKs part of the agent domain, or claim exactly-once delivery from an external messaging provider that does not offer an idempotency token.

## Decision

### Prompt lifecycle

Prompt definitions and prompt versions are separate tenant-owned entities.

A prompt has at most one `draft` version and at most one `published` version at a time. Published and archived versions are immutable. Publishing a draft atomically archives the previous published version and promotes the draft.

Agents reference a prompt definition, not a mutable text field. At the start of an agent run, the runtime snapshots the currently published prompt-version ID. Retries for that run continue using the same version even if the tenant later publishes another version.

### Agent assignment

The first routing primitive is an explicit tenant-scoped assignment from one channel account to one agent. A channel account has at most one assigned agent in Milestone 6.

This is intentionally narrower than a generic routing rules engine. Conversation-specific overrides, specialist-agent routing, weighted routing, and policy-driven routing can extend this boundary later without putting routing logic into channel adapters.

### Prompt composition

The runtime composes distinct layers instead of concatenating arbitrary message content into system instructions:

1. platform runtime policy;
2. the tenant's published prompt version;
3. deterministic channel instructions;
4. bounded conversation history and the current customer message as model input.

Conversation/customer text is never promoted into the trusted instruction layers.

### Durable agent run

Every eligible inbound customer text message has at most one `AgentRun` per tenant.

The run snapshots the selected agent, prompt version, conversation, channel account, and inbound message. It persists a generated response before external channel dispatch. After successful canonical outbound persistence, the temporary generated response is cleared and the outbound message ID is retained.

A short PostgreSQL-backed lease prevents two workers from generating/sending the same run concurrently. While AI generation remains in flight, the runtime periodically renews the lease without holding a database transaction open. An expired lease can be reclaimed after a worker crash.

Redis remains delivery infrastructure only. PostgreSQL remains the source of truth for run state and idempotency.

### Failure semantics

AI Gateway retry/fallback remains responsible for provider attempts inside one generation operation. The final routing error exposes whether another worker retry can reasonably help.

Retryable AI/channel failures keep the run resumable and leave the Redis job pending for reclaim. Deterministic configuration/output errors mark the run failed and allow the queue entry to be acknowledged.

Telegram `sendMessage` still cannot provide exactly-once external delivery. A crash after Telegram accepts a message but before PostgreSQL commits may produce at-least-once delivery on retry, as already documented by Milestone 5. Milestone 6 does not hide or falsely eliminate that provider limitation.

## Alternatives Considered

### Store one mutable prompt string directly on Agent

Rejected. It removes publish/rollback semantics, makes in-flight retries non-deterministic, and prevents auditable version history.

### Put the active prompt-version ID directly on Agent

Rejected for the initial lifecycle. Publishing would require a second activation action for every agent and would make one shared prompt harder to manage. Agents instead reference the prompt definition, while each run snapshots the published version it actually used.

### Hold the AgentRun transaction/row lock across AI generation

Rejected. It would keep database transactions/connections open across slow AI I/O and reduce runtime resilience. AgentRun ownership instead uses a renewable lease. Outbound dispatch continues to use Milestone 5's existing per-idempotency advisory transaction serialization inside the channel runtime; ADR-012 does not redefine that transport boundary.

### Use Redis locks/run state

Rejected. Redis is not the source of truth and must not become necessary to reconstruct whether an AI response was generated or dispatched.

### Build a generic routing/policy DSL now

Rejected as premature. Milestone 13 owns deterministic policy rules, and advanced agent routing is post-MVP unless justified earlier.

## Consequences

- Published prompt history is durable and immutable.
- In-flight runs remain stable across prompt publication changes.
- Duplicate/reclaimed queue work does not normally duplicate AI generation or dispatch.
- The runtime gains a small amount of PostgreSQL state (`AgentRun`) to make retries observable and resumable.
- Channel adapters remain unaware of agents and prompts.
- The worker becomes the orchestration boundary between channel processing and agent processing.
- Exactly-once delivery is not guaranteed by providers that do not support it.

## Follow-up Implications

- Milestone 7 Website Chat can reuse the same channel-account assignment and agent runtime.
- Milestone 8 Tool Runtime can extend the agent execution path without changing channel adapters.
- Milestone 9 RAG and Milestone 12 Customer Memory can add bounded context layers to the prompt composer.
- Milestone 11 Human Handoff can suppress autonomous runs before generation/dispatch.
- Milestone 13 Policy Engine can replace hard-coded deterministic autonomy/routing restrictions with policy evaluation.
