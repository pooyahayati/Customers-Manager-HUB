# Milestone 11 — Human Handoff & Operator Inbox

Status: Implementation specification

## Goal

Allow a tenant conversation to move safely between autonomous AI and a human operator without duplicate or competing customer responses.

## Scope

- Tenant-scoped conversation handoff lifecycle.
- Human queue / operator inbox API.
- Claim, release, resolve, and resume-AI transitions.
- Human outbound replies through the conversation's originating channel.
- Autonomous AI pause while a handoff is active.
- AI assist-only suggestions that are never dispatched automatically.
- Deterministic escalation rules for customer keywords and high-risk tool approval.
- Agent-run pause/resume foundation for approval-driven handoffs.
- Audit events and cross-tenant negative tests.

## Non-goals

- Workforce scheduling, SLA routing, skill-based routing, or team queues.
- Full policy engine. Broader autonomy/business rules remain M13.
- Rich admin/operator UI. M11 exposes the stable operator inbox API contract; the current web app remains a thin shell.
- Realtime websocket presence/typing indicators.
- Multi-operator collaborative editing.

## Core state model

A `ConversationHandoff` is the lifecycle record. Only one active handoff may exist for a conversation.

Statuses:

- `queued` — AI autonomy is paused and the conversation is waiting for an operator.
- `claimed` — AI autonomy remains paused and one operator owns the conversation.
- `resolved` — handoff is closed; future inbound messages may use autonomous AI again.
- `cancelled` — handoff is closed without operator resolution.

Allowed transitions:

```text
none -> queued
queued -> claimed
claimed -> queued        # release
queued -> resolved       # supervisor/admin or explicit resume
claimed -> resolved      # claimant or supervisor/admin
queued -> cancelled
claimed -> cancelled
```

A PostgreSQL partial unique index enforces at most one active (`queued` or `claimed`) handoff per conversation.

## Ownership and authorization

Operator-capable roles:

- owner
- admin
- supervisor
- agent

Rules:

- Owner/Admin/Supervisor/Agent may request a manual handoff.
- Owner/Admin/Supervisor/Agent may claim an unclaimed queued conversation.
- Only the current claimant may send a normal operator reply or release the handoff; Owner/Admin/Supervisor may override.
- Owner/Admin/Supervisor may cancel or force-resume any active handoff.
- A claimed Agent may resolve/resume the conversation they own.
- Analyst/Viewer are read-only where tenant read access is already permitted; they cannot mutate handoff state or send replies.

## Human outbound replies

Operator replies must use the canonical `dispatch_text()` channel runtime with:

- the tenant-scoped `ConversationChannelBinding`;
- `MessageAuthorType.HUMAN`;
- a server-generated deterministic idempotency key based on handoff ID + client request key;
- the same encrypted channel credentials and outbound adapter contracts used by AI replies.

No direct Telegram/Website provider call is allowed from the handoff module.

## AI pause invariant

Before creating or claiming an `AgentRun`, Agent Runtime checks for an active handoff for the same tenant + conversation.

If active:

- no customer-response generation is invoked;
- no RAG/tool execution is invoked;
- no outbound AI message is dispatched;
- the channel job may still be acknowledged because the canonical inbound message is already durable in PostgreSQL.

This invariant is application-side and does not depend on prompting.

## Agent run pause/resume

M11 extends `AgentRunStatus` with `paused`.

A run may enter `paused` when a runtime event requires human control, primarily a configured high-risk tool approval handoff.

Paused runs:

- hold no lease;
- have no generated customer reply;
- remain linked to the original inbound message/event;
- can be reset to `pending` when the handoff is resolved with `resume_ai=true`.

When a paused run is resumed, the API re-enqueues its original `ChannelInboundEvent`. Existing tool execution idempotency (`agent_run_id`, `call_ordinal`) remains authoritative, so an approved tool execution can continue safely if the model requests the same tool call again.

## AI assist-only suggestions

An operator with an active handoff may request an AI suggestion.

The assist flow:

1. builds the normal bounded conversation context;
2. reuses the assigned agent's published prompt when available;
3. invokes the existing `customer_response` AI task;
4. persists an `OperatorAssistSuggestion` record with requester and generated text;
5. never dispatches to the customer;
6. never changes handoff ownership/status.

The suggestion is untrusted draft text. The operator must explicitly send a human reply.

## Escalation rules

M11 implements a deliberately small deterministic `HandoffPolicy` per tenant.

Fields:

- `enabled`
- `customer_keywords` — bounded case-insensitive substring triggers
- `pause_on_tool_approval`

Rules are evaluated application-side:

- after inbound channel/voice processing and before autonomous Agent Runtime;
- during tool execution when the tool result becomes `approval_required`.

Broader intent, business-hours, confidence, SLA, and policy combinations belong to M13.

## Tool approval integration

When `pause_on_tool_approval=true` and a tool result is `approval_required`:

- create/reuse an active handoff with reason `tool_approval_required`;
- persist the source `agent_run_id` and tool execution ID;
- pause the agent run;
- do not ask the model to produce a fallback customer answer in the same run.

Approving/denying the tool remains the existing Tool Runtime API responsibility. Handoff resolution does not silently approve a tool.

## Operator inbox API

Prefix:

`/api/v1/tenants/{tenant_id}/operator-inbox`

Required endpoints:

- `GET /` — queued/claimed conversations with contact, channel, assignment, reason, timestamps, and last-message preview.
- `GET /{conversation_id}` — operator conversation view with handoff and bounded message history.
- `POST /{conversation_id}/handoff` — manual queue request.
- `PUT /{conversation_id}/claim` — atomically claim a queued handoff.
- `PUT /{conversation_id}/release` — release back to queue.
- `PUT /{conversation_id}/resolve` — close handoff and optionally resume a paused agent run.
- `POST /{conversation_id}/reply` — send a human outbound text reply.
- `POST /{conversation_id}/assist` — generate/persist an assist-only draft.
- `GET /handoff-policy` and `PUT /handoff-policy` — read/configure deterministic escalation policy.

## Concurrency and idempotency

- Claim uses row locking and succeeds only from `queued`.
- Repeated manual handoff requests reuse the existing active handoff.
- Partial unique index prevents two active handoffs under races.
- Human reply requires a client idempotency key and reuses canonical outbound idempotency protection.
- Resolve/release transitions are row-locked and validate the current status/owner.
- AI pause checks are tenant-scoped and performed before generation.

## Audit

Audit actions:

- `handoff.requested`
- `handoff.claimed`
- `handoff.released`
- `handoff.resolved`
- `handoff.cancelled`
- `handoff.policy.updated`
- `handoff.operator_reply`
- `handoff.assist.generated`

Audit details must not include channel/provider credentials or hidden prompts.

## Acceptance criteria

M11 is complete when:

1. A manual or deterministic escalation can queue a conversation.
2. While queued/claimed, autonomous AI cannot generate or dispatch a competing response.
3. An authorized operator can atomically claim and release the conversation.
4. Only the claimant (or supervisor/admin/owner override) can send a human reply.
5. Human replies use the originating channel through canonical channel runtime.
6. AI assist produces a draft only and never auto-dispatches.
7. A handoff can be resolved and AI autonomy resumes for future inbound messages.
8. Tool approval can pause an Agent Run and create a handoff without approving the tool itself.
9. A paused run can be safely re-enqueued after explicit resume.
10. Cross-tenant IDs return not-found semantics and cannot alter another tenant's queue.
11. Migration, Alembic drift, static, integration, and Docker regressions pass.
