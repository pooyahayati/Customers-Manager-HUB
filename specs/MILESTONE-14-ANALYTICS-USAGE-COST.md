# Milestone 14 — Analytics, Usage & Cost

## Status

Implementation specification for M14.

## Goal

Make tenant operational performance and AI consumption measurable without introducing a second analytics event pipeline or data warehouse before the MVP requires one.

M14 builds read models from the canonical transactional records already produced by M0–M13 and adds only the minimum pricing configuration required to normalize AI cost.

## Scope

M14 implements:

- tenant-scoped operational analytics overview;
- per-provider/model/task AI usage metrics;
- token and audio billable-unit aggregation;
- configurable tenant/model pricing and estimated USD cost;
- conversation and active-contact counts;
- first-response-time measurement;
- resolution-time foundation;
- human-handoff rate;
- AI automation rate;
- tool success/failure metrics;
- lightweight admin dashboard foundation;
- tenant isolation, RBAC, and audit coverage for pricing changes.

## Non-goals

M14 does not introduce:

- a data warehouse;
- OLAP infrastructure;
- a second event bus for analytics;
- external billing or invoicing;
- provider-price web scraping;
- subscription/quota enforcement;
- long-term pre-aggregated rollups;
- arbitrary user-defined analytics queries.

## Source-of-truth model

Analytics reads directly from existing canonical tables:

- `ai_execution_traces` for AI requests, provider/model/task, latency, tokens, and audio seconds;
- `conversations` and `messages` for conversation/contact activity, response timing, and AI/human authorship;
- `conversation_handoffs` for handoff rate and exact human-handoff duration;
- `tool_executions` for tool execution success/failure and duration.

No duplicated analytics event is emitted for data that already exists transactionally.

## AI pricing model

M14 adds `ai_model_pricing`.

Pricing is tenant scoped because provider contracts, negotiated rates, and effective prices may differ by tenant.

Fields:

- `tenant_id`;
- `provider`;
- `model_id`;
- `input_per_million_usd`;
- `output_per_million_usd`;
- `audio_per_minute_usd`;
- `effective_from`;
- `created_by_user_id`;
- timestamps.

Multiple effective-dated rows are allowed for one provider/model. Historical traces use the newest pricing row whose `effective_from <= trace.created_at`.

Pricing is configuration, not provider truth. M14 does not hard-code vendor prices.

## Cost calculation

For one AI execution trace:

```text
input_cost = input_tokens / 1_000_000 * input_per_million_usd
output_cost = output_tokens / 1_000_000 * output_per_million_usd
audio_cost = audio_seconds / 60 * audio_per_minute_usd
estimated_cost = input_cost + output_cost + audio_cost
```

Rules:

- costs are estimates based on configured rates;
- `Decimal`/database `NUMERIC` is used for rates and aggregation;
- failed provider attempts are included when they report billable usage;
- a trace with consumed units but no applicable configured rate is reported as unpriced rather than guessed;
- zero/absent consumption contributes zero cost.

## Reporting window

Analytics endpoints accept optional `from` and `to` timestamps.

- timestamps must include timezone offsets;
- defaults to the preceding 30 days;
- `to` is exclusive;
- maximum report window is 366 days;
- all comparisons are normalized to UTC.

## Operational metric semantics

### Conversations

Count conversations created inside the reporting window.

### Unique contacts

Count distinct contacts with at least one inbound customer message inside the reporting window.

### First response time

For each conversation whose first inbound customer message occurs in the reporting window, measure to the first later outbound AI or human message. The dashboard reports average response seconds and sample count.

### Resolution time

For conversations currently `resolved` or `closed`, M14 reports `updated_at - created_at` when the closing update falls in the reporting window. This is the current canonical timestamp foundation; a dedicated lifecycle event can replace it later without changing the analytics API.

Human handoff duration is separately exact using `requested_at` to `closed_at`.

### Human handoff rate

Conversation-level rate:

```text
conversations with a handoff requested in window
/
conversations with inbound customer activity in window
```

### AI automation rate

Conversation-level rate among conversations with inbound customer activity in the window.

A conversation is considered automated when it has an AI outbound response and has neither a human outbound response nor a human handoff in the same reporting window.

### Tool metrics

Tool executions created in the reporting window are grouped by status and tool. Success rate uses terminal `succeeded` and `failed` executions; denied/approval-required states are reported separately.

## API surface

Tenant-scoped API:

- `GET /api/v1/tenants/{tenant_id}/analytics/overview`
- `GET /api/v1/tenants/{tenant_id}/analytics/ai-usage`
- `GET /api/v1/tenants/{tenant_id}/analytics/tools`
- `GET /api/v1/tenants/{tenant_id}/analytics/pricing`
- `PUT /api/v1/tenants/{tenant_id}/analytics/pricing`

Pricing mutation requires Owner/Admin. Analytics read access follows authenticated tenant membership.

## Dashboard foundation

The web admin receives a lightweight `/analytics` route with the KPI vocabulary and API contract surfaced as the first dashboard shell. Authentication/session UX remains outside M14; the API remains the authoritative source.

## Security and privacy

- all queries are tenant scoped;
- no message text, prompts, credentials, tool inputs, or memory values are returned in analytics responses;
- pricing writes are Owner/Admin only and write `AuditEvent` records;
- analytics does not weaken existing authorization boundaries;
- cross-tenant pricing and analytics access must fail.

## Performance

M14 intentionally uses direct aggregation for the MVP.

- report windows are bounded to 366 days;
- existing tenant/timestamp indexes are reused;
- pricing has a tenant/provider/model/effective-date index;
- no background rollup is introduced until measured production load justifies it.

## Validation

Acceptance validation must include:

- Ruff and formatter;
- Pyright strict;
- migration upgrade and Alembic drift check;
- pricing CRUD/RBAC/tenant-isolation tests;
- effective-date pricing tests;
- AI token/audio/cost aggregation tests;
- conversation/contact/response/handoff/automation metric tests;
- tool metric tests;
- proof that analytics responses do not expose payload content;
- full backend unit/integration regression;
- frontend lint/typecheck/build;
- Docker/Compose migration/readiness/non-root runtime.

## Exit gate

A tenant can retrieve operational performance indicators and AI usage/cost indicators from canonical project data with deterministic tenant isolation and configurable pricing.