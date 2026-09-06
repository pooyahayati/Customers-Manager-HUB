# Milestone 6 Specification — Agent & Prompt Runtime

## Status

Implementation specification for Roadmap Milestone 6.

## Goal

Allow a tenant to configure a versioned prompt, attach it to an agent, assign that agent to a Telegram channel account, and automatically answer an inbound Telegram text message through the configured `customer_response` AI task profile without introducing channel-specific logic into the AI/agent core.

## In Scope

### Agent domain

- Tenant-owned `Agent` entity.
- Agent name, optional description, active/inactive state, and prompt definition reference.
- Tenant-scoped Admin APIs for create/list/read/update.
- Owner/Admin are the only roles allowed to mutate agent configuration.
- Agent configuration changes append non-sensitive audit events.

### Prompt domain and lifecycle

- Tenant-owned prompt definition.
- Immutable version history after publication.
- One mutable draft version at a time.
- One published version at a time.
- Publishing a draft atomically archives the previous published version.
- Creating/updating a draft after publication creates the next monotonically increasing version.
- Prompt content is never included in audit details.
- Owner/Admin are the only roles allowed to mutate/publish prompts.

### Agent assignment

- Explicit tenant-scoped mapping from a channel account to one Agent.
- One channel account has at most one assigned Agent in this milestone.
- Assign/unassign operations are Owner/Admin only and audited.
- Channel adapters do not know about Agent IDs, Prompt IDs, or AI task profiles.

### Prompt composition

Compose the customer-response operation from distinct layers:

1. platform runtime policy;
2. tenant published prompt content;
3. deterministic channel instruction;
4. bounded canonical conversation history;
5. current inbound customer text.

Conversation/customer content must remain model input and must not be promoted into trusted instruction layers.

For Telegram, the channel instruction requires a plain-text response no longer than Telegram's 4096-character text limit.

### Conversation context

The context builder:

- reads only the current tenant/conversation;
- excludes the current inbound message from history;
- includes a bounded number of recent text messages;
- keeps chronological order in the final input;
- labels canonical authors (`customer`, `human`, `ai`, `system`);
- enforces a character budget rather than introducing a tokenizer dependency;
- does not include attachments, raw Telegram payloads, credentials, audit data, or provider traces.

### AI customer-response orchestration

For eligible inbound text messages:

1. Resolve the canonical inbound event/message and originating channel account.
2. Resolve the channel account's assigned active Agent.
3. Resolve the Agent's currently published prompt version.
4. Create or resume a durable `AgentRun` keyed to the inbound message.
5. Snapshot the selected Agent and prompt-version IDs on the run.
6. Build bounded conversation context.
7. Call `AIGateway.generate()` with task type `customer_response`.
8. Validate the generated output as non-blank plain text within the channel limit.
9. Persist the generated text to the run before outbound transport.
10. Dispatch through the generic channel runtime using an Agent-run idempotency key.
11. Persist the canonical outbound Message as `author_type=ai`.
12. Mark the AgentRun succeeded, retain the outbound message ID, and clear the temporary generated text.

No provider SDK/import is allowed in Agent domain/runtime code.

### Durable run and retry semantics

`AgentRun` is PostgreSQL source-of-truth orchestration state.

Required states:

- `pending`
- `generated`
- `succeeded`
- `failed`

The run stores:

- tenant ID;
- Agent ID;
- prompt-version ID;
- channel-account ID;
- conversation ID;
- inbound message ID;
- optional outbound message ID;
- temporary generated text while dispatch is incomplete;
- current error code when applicable;
- a short lease token/expiry for concurrent-worker exclusion;
- timestamps.

There is one AgentRun per tenant/inbound message.

A worker must not hold a database transaction open across AI/provider network calls. Instead it claims the run with a lease, performs external work, and commits state transitions between stages. An expired lease may be reclaimed.

Retryable AI/channel failures keep the run resumable and leave the Redis stream entry pending. Deterministic configuration/output failures mark the run failed and allow the queue entry to be acknowledged.

### Worker integration

The existing channel worker becomes the orchestration boundary:

1. perform/confirm channel-event processing;
2. for canonical inbound customer text, run Agent orchestration;
3. acknowledge the Redis job only when channel processing and any required Agent work are complete or terminally skipped/failed;
4. leave retryable failures pending for `XAUTOCLAIM`.

Voice messages remain channel-processed only in Milestone 6. Voice transcription and continuation into Agent Runtime are Milestone 10.

### AI routing failure semantics

`AIRoutingError` must expose a retryability flag in addition to its canonical code so worker orchestration can distinguish deterministic configuration failures from potentially transient provider exhaustion.

Existing provider retry/fallback behavior remains inside the AI Gateway.

## Data Model

New tables:

- `agents`
- `agent_prompts`
- `agent_prompt_versions`
- `agent_channel_assignments`
- `agent_runs`

No provider-specific or Telegram-specific Agent table is introduced.

## API Surface

Prompt Admin API:

- `POST /api/v1/tenants/{tenant_id}/prompts`
- `GET /api/v1/tenants/{tenant_id}/prompts`
- `GET /api/v1/tenants/{tenant_id}/prompts/{prompt_id}`
- `PUT /api/v1/tenants/{tenant_id}/prompts/{prompt_id}/draft`
- `POST /api/v1/tenants/{tenant_id}/prompts/{prompt_id}/publish`

Agent Admin API:

- `POST /api/v1/tenants/{tenant_id}/agents`
- `GET /api/v1/tenants/{tenant_id}/agents`
- `GET /api/v1/tenants/{tenant_id}/agents/{agent_id}`
- `PATCH /api/v1/tenants/{tenant_id}/agents/{agent_id}`
- `GET /api/v1/tenants/{tenant_id}/agents/assignments/channels/{channel_account_id}`
- `PUT /api/v1/tenants/{tenant_id}/agents/assignments/channels/{channel_account_id}`
- `DELETE /api/v1/tenants/{tenant_id}/agents/assignments/channels/{channel_account_id}`

## Security Requirements

- All Agent/Prompt/Admin assignment reads/writes are tenant-scoped through existing server-side membership context.
- Cross-tenant IDs return non-leaking 404 behavior where applicable.
- Only Owner/Admin can mutate/publish/assign.
- Prompt content is not written to audit events or AI execution traces.
- Generated response content is stored only as canonical Message content and temporarily in an unfinished AgentRun; it is cleared after successful outbound persistence.
- Conversation history supplied to the model is bounded and tenant/conversation scoped.
- Credentials remain resolved exclusively by AI/channel provider boundaries; Agent code never receives raw provider configuration except through those calls.
- Customer message text is never inserted into trusted instruction layers.

## Output Validation

Customer-response output must:

- be a string returned by the GenerationResult;
- be non-blank after trimming;
- contain no NUL byte;
- be no longer than the originating channel's supported text limit (4096 for Telegram in Milestone 6).

Invalid output is a deterministic terminal AgentRun failure and must not be dispatched.

## Explicitly Out of Scope

- Website Chat transport.
- Voice transcription.
- RAG/Knowledge Base.
- Customer memory.
- Tool calls/function calling.
- Policy Engine.
- Human handoff state machine.
- Multiple agents per channel/routing rules DSL.
- Dynamic specialist-agent delegation.
- Prompt variables/template language beyond deterministic composition.
- Admin UI/Simulation Console.
- Exactly-once Telegram external delivery.

## Acceptance Tests

Automated coverage must include at least:

1. Owner/Admin can create prompts/agents and assign an Agent; Viewer/Analyst/Supervisor/Agent roles cannot mutate this configuration.
2. Cross-tenant Prompt/Agent/assignment access is blocked.
3. Prompt starts as v1 draft; publishing makes it immutable/current; a later draft becomes v2 and publishing archives v1.
4. At most one draft and one published version exist per prompt under concurrent mutation.
5. Agent references only a same-tenant Prompt.
6. Assignment references only same-tenant Agent and ChannelAccount and one Agent per ChannelAccount.
7. Prompt composer keeps tenant instructions separate from customer conversation text and applies the context budget.
8. Blank/oversized/NUL model outputs are rejected and never dispatched.
9. A Telegram text webhook enters the Redis channel job, worker processing invokes a mock `customer_response` task profile, and the generated response is sent through the generic Telegram adapter.
10. The resulting outbound canonical Message has `direction=outbound`, `author_type=ai`, the provider-generated text, and a stable Agent-run idempotency key.
11. AgentRun snapshots the published prompt version used for the response and reaches `succeeded` with generated text cleared.
12. Reprocessing/reclaiming the same inbound event does not invoke AI or Telegram send again after success.
13. A generated-but-not-yet-dispatched run resumes using persisted generated text without another AI call.
14. Retryable AI/channel failure remains resumable; deterministic configuration/output failure becomes terminal.
15. AI execution trace is still created through the configured task profile without storing prompt/customer/output content.
16. Migration applies from `0004`, `alembic check` reports no drift, and full Docker Compose runtime still starts API/Web/Worker/PostgreSQL/Redis with API/Worker non-root.

## Exit Gate

A tenant can configure and publish a prompt, create an Agent, assign it to a Telegram channel account, configure the `customer_response` task profile, receive a Telegram text message, and obtain one canonical AI-authored outbound Telegram reply through the existing worker/AI/channel boundaries without duplicate AI/provider calls on a normal retry.
