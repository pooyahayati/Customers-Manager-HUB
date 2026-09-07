# ADR-017 — Conversation Handoff State Machine and Operator Control Boundary

Status: Accepted

## Context

Customers Manager HUB already has canonical conversations/messages, channel-independent outbound dispatch, durable Agent Runs, tool approval foundations, and tenant RBAC. Milestone 11 must let human operators take control without allowing AI and humans to respond competitively to the same conversation.

A handoff implementation could be modeled as a boolean on `Conversation`, a separate ticketing subsystem, or an explicit lifecycle record.

## Decision

1. Human control is represented by a tenant-scoped `ConversationHandoff` lifecycle record rather than a prompt instruction or a single conversation boolean.
2. At most one active handoff (`queued` or `claimed`) may exist per conversation; PostgreSQL enforces this with a partial unique index.
3. Active handoff presence is the application-side AI autonomy gate. Agent Runtime must check it before generation/tool/RAG work.
4. Claim/release/resolve transitions are transactional and row-locked.
5. Human replies reuse canonical `dispatch_text()` and `MessageAuthorType.HUMAN`; handoff code never calls channel providers directly.
6. Operator AI assistance is draft-only. Suggestions are persisted separately and are never dispatched automatically.
7. A minimal tenant `HandoffPolicy` provides deterministic keyword escalation and optional pause on high-risk tool approval. The broad policy engine remains M13.
8. `AgentRun` gains a `paused` status so approval-driven handoffs can preserve the original run identity and tool idempotency boundary.
9. Explicit resume may reset a paused run to `pending` and re-enqueue its original inbound channel event. Resolving a handoff never implicitly approves a tool.
10. The operator inbox is introduced first as a stable API contract. A richer staff UI may be added later without changing the control-state model.

## Alternatives considered

### Add `human_controlled=true` to Conversation

Rejected. It does not preserve handoff history, claimant identity, reason/provenance, or safe claim/release concurrency.

### Build a standalone ticket/helpdesk domain

Rejected for M11. It duplicates conversation ownership and adds lifecycle complexity before the product requires ticket entities, SLA queues, or workforce management.

### Enforce handoff only through agent prompts

Rejected. Prompt-only autonomy control cannot prevent a model/runtime race and violates the requirement that critical control be enforced application-side.

### Let operator replies bypass channel runtime

Rejected. It would duplicate Telegram/Website credential, idempotency, and persistence behavior and could break omnichannel guarantees.

### Automatically resume/approve high-risk tools when a handoff is resolved

Rejected. Handoff ownership and tool authorization are separate security decisions. Existing Tool Runtime approval remains authoritative.

## Consequences

- Conversation ownership has a durable auditable history.
- AI pause behavior is deterministic and provider-independent.
- Human replies inherit existing channel security/idempotency behavior.
- Tool approval workflows can pause/resume Agent Runs without creating another orchestration service.
- The database adds handoff, policy, and assist-suggestion tables plus one Agent Run status value.
- Operator API mutations require explicit RBAC and claimant checks.

## Follow-up implications

- M13 may supersede the minimal `HandoffPolicy` with a broader policy evaluation engine while preserving handoff runtime contracts.
- M14 can derive handoff rate, queue time, claim time, and human resolution metrics from durable handoff records.
- Advanced team routing, SLA/workforce management, realtime presence, and collaborative inbox features remain post-MVP candidates unless validation requires them earlier.
