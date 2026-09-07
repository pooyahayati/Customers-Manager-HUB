# Milestone 8 — Tool Runtime & Live Business Data

Status: Implementation specification

## Goal

Allow an assigned AI agent to request tenant-owned business tools while the application independently validates, authorizes, executes, traces, and returns safe structured results. Raw credentials must never enter model context.

## Scope

- Canonical tool definitions and registry.
- JSON Schema input/output validation.
- Read/write operation classification.
- Risk levels and approval requirement foundation.
- Encrypted tenant tool credentials.
- Explicit tenant/agent tool authorization.
- Generic REST adapter for fixed, administrator-configured HTTPS endpoints.
- Read-only business/product reference adapter.
- Read-only Google Sheets values lookup path.
- Tool execution traces and audit events.
- Timeout/retry/idempotency behavior.
- Agent Runtime structured tool-request loop.
- Admin APIs for tool configuration, credentials, permissions, execution trace inspection, and approval decisions.

## Out of scope

- Full deterministic Policy Engine (M13).
- Operator approval inbox/queue and resumable approval workflow (M11/M13).
- OAuth authorization-code/device flows and automatic token refresh.
- Arbitrary model-selected URLs, SQL, shell commands, code execution, GraphQL generation, or webhook scripting.
- Tool marketplace/discovery across tenants.
- Production-wide rate limiting and advanced egress proxying (M15).
- RAG/knowledge retrieval (M9).

## Core invariants

1. A model tool request is data, never authorization.
2. Every executable tool belongs to one tenant.
3. An agent may execute only tools explicitly assigned to that same tenant/agent.
4. A tool must be active and its input must validate before credential resolution or external I/O.
5. Credentials are encrypted at rest with the deployment `ENCRYPTION_KEY`, resolved only immediately before execution, and never returned by APIs or sent to the model.
6. Tool output is untrusted external input and must validate against the configured output schema before it can re-enter model context.
7. Generic REST URLs are fixed by administrator configuration. The model cannot provide or override scheme, host, port, path, or authentication headers.
8. High/critical risk or explicitly approval-required tools do not execute autonomously in M8.
9. Mutating retries require a configured idempotency header; otherwise write attempts are limited to one external request.
10. Execution is tenant-scoped, traceable, bounded by timeout/attempt limits, and retry-safe at the platform level.

## Data model

### ToolDefinition

Tenant-owned tool metadata:

- `id`, `tenant_id`
- `name`, `version`, `description`
- `adapter_kind`: `generic_rest | business_reference | google_sheets`
- `operation_type`: `read | write`
- `risk_level`: `low | medium | high | critical`
- `input_schema`, `output_schema` (JSONB)
- `configuration` (non-secret adapter configuration only)
- `timeout_seconds` (1..60)
- `max_attempts` (1..3)
- `requires_approval`
- `is_active`
- timestamps

Tool `(tenant_id, name, version)` is unique.

### ToolCredential

One optional encrypted auth binding per tool:

- `tenant_id`, `tool_id`
- `auth_type`: `bearer | header`
- optional non-secret `header_name`
- `ciphertext`, `nonce`, `key_version`
- timestamps

The plaintext secret is accepted only on write and never returned.

### AgentToolPermission

Explicit allowlist relation:

- `tenant_id`, `agent_id`, `tool_id`
- timestamps

No row means deny.

### ToolExecution

Durable execution/audit trace:

- `tenant_id`, `tool_id`, `agent_id`, optional `agent_run_id`
- stable `idempotency_key`
- status: `pending | approval_required | running | succeeded | failed | denied`
- approval status: `not_required | pending | approved | denied`
- validated input snapshot
- validated output snapshot when successful
- `error_code`, `attempt_count`, `duration_ms`
- approval actor/time when applicable
- timestamps

`(tenant_id, idempotency_key)` is unique.

Sensitive HTTP headers and credential plaintext are never persisted in trace/audit details.

## JSON Schema contract

Use `jsonschema` Draft 2020-12 validation for runtime-defined schemas.

Configuration-time validation requirements:

- schemas must be JSON objects;
- schema definitions themselves must be valid Draft 2020-12 schemas;
- remote `$ref` is rejected; tool schemas are self-contained;
- serialized schema size is bounded;
- input and output values are bounded before persistence/model reinjection.

Runtime invalid input is denied before external execution. Runtime invalid output fails the execution and is not supplied to the model as a successful tool result.

## Adapter contracts

Canonical adapter interface receives only trusted runtime objects:

- tool definition/configuration;
- validated arguments;
- server-resolved credential binding;
- stable execution idempotency token;
- configured timeout.

It returns a JSON-compatible structured value or a typed retryable/non-retryable adapter error.

### Generic REST

Configuration:

- fixed `url` using HTTPS;
- fixed `method`;
- `argument_location`: `query | json`;
- optional non-secret static headers;
- optional `idempotency_header` for writes.

Security:

- no redirects;
- no URL supplied by model;
- no userinfo;
- literal IP hosts must be globally routable;
- DNS preflight rejects loopback/private/link-local/reserved/multicast/unspecified resolutions;
- sensitive authorization/cookie/proxy headers are forbidden from static configuration;
- credential auth is injected only by the trusted adapter.

For read tools, GET is required. Write classification is required for POST/PUT/PATCH/DELETE.

### Business reference

A constrained live-data adapter intended for product/catalog/business lookups:

- fixed administrator-owned HTTPS endpoint;
- GET only;
- read-only classification;
- validated arguments encoded as query parameters;
- same SSRF/credential/output validation boundary as Generic REST.

### Google Sheets

M8 provides a real read-only integration path for `spreadsheets.values.get`:

- configured `spreadsheet_id`;
- model supplies only a validated `range` argument (and optional value render parameters permitted by schema/config);
- request host is fixed to `sheets.googleapis.com`;
- bearer credential is resolved server-side;
- writes and OAuth refresh flows are deferred.

## Authorization and risk

Before external I/O the runtime checks, in order:

1. tenant/tool existence;
2. tool active;
3. agent exists, same tenant, active;
4. explicit `AgentToolPermission` exists;
5. input schema valid;
6. approval rule;
7. credential requirements/config validity;
8. adapter execution.

M8 approval rule:

- `requires_approval=true` -> approval required;
- `risk_level in {high, critical}` -> approval required;
- otherwise execution may proceed.

This is a safety floor. M13 can add stricter tenant policies later without weakening this rule.

Owner/Admin can approve or deny a pending execution record. M8 records the decision but does not yet provide the M11 operator queue/resume lifecycle.

## Retry and idempotency

- Each agent tool request uses a stable idempotency key derived from `agent_run_id` plus tool-loop call ordinal.
- Reprocessing a succeeded execution returns the stored validated result and performs no new external call.
- Concurrent duplicate execution is serialized by the unique idempotency constraint/runtime claim.
- Reads may retry retryable network/5xx failures up to `max_attempts`.
- Writes may retry only when an `idempotency_header` is configured; the runtime injects a stable execution token into that header.
- 4xx business responses are non-retryable except explicitly transient statuses such as 408/429.

## Agent Runtime integration

If an agent has no allowed active tools, M6 behavior remains unchanged.

If tools are available:

1. Runtime builds a model-visible catalog containing only stable tool name/version, description, operation/risk metadata, and input schema.
2. Customer-response generation uses a provider-neutral structured response schema with either:
   - `final` + text, or
   - `tool_call` + tool name + arguments.
3. Runtime validates the structured response independently.
4. A requested tool is resolved by exact allowed catalog name and executed by `ToolRuntime`.
5. A safe structured tool result/status is appended to controlled context and generation repeats.
6. Loop is bounded to prevent infinite tool recursion.
7. Final customer text passes existing channel output validation and dispatch.

Tool credentials, request headers, internal error details, and raw exception text are never included in model context.

## Admin APIs

Tenant-scoped authenticated endpoints under `/api/v1/tenants/{tenant_id}`:

- create/list/read/update tools;
- set/delete tool credential;
- assign/unassign/list agent tool permissions;
- list/read tool execution traces;
- approve/deny approval-required execution records.

Owner/Admin mutate tool configuration, credentials, permissions, and approvals. Read access follows existing tenant authenticated administrative conventions.

Cross-tenant resource IDs return 404 rather than leaking existence.

## Auditing

Audit configuration mutations:

- tool created/updated;
- credential set/deleted (metadata only, never secret);
- agent permission granted/revoked;
- approval approved/denied.

Tool execution trace is the primary runtime audit record. Failures record stable error codes, never secrets/raw headers.

## Tests

Required coverage:

- JSON Schema definition/input/output validation;
- tenant isolation and RBAC negatives;
- encrypted credential round-trip and no plaintext API/log/trace exposure;
- explicit agent permission allow/deny;
- inactive/missing tool and agent denial;
- approval floor for high/critical/write configurations;
- execution idempotency and concurrent/retry behavior;
- REST fixed endpoint and SSRF rejection;
- timeout, retryable 429/5xx, non-retryable 4xx;
- write retry only with idempotency header;
- business reference adapter contract;
- Google Sheets URL/range/auth contract with mocked HTTP transport;
- Agent Runtime no-tool regression;
- Agent Runtime tool request -> execute -> result -> final response;
- denied/approval-required tool never performs external I/O;
- migrations/Alembic drift;
- Docker image/Compose/non-root regression.

No default test may require a real paid AI or external business API.

## Acceptance criteria

Milestone 8 is complete when:

1. A tenant can configure a schema-defined read tool and encrypted credential.
2. A tenant can explicitly assign that tool to an agent.
3. The agent can request the tool through the customer-response runtime.
4. The application independently validates tenant, agent permission, risk/approval, arguments, credentials, and output.
5. A permitted tool executes through a canonical adapter and the validated result can influence the final customer response.
6. A denied or approval-required tool does not execute externally.
7. No raw credential is visible to the model or returned by read APIs/traces.
8. Generic REST, business reference, and Google Sheets read paths have deterministic contract tests.
9. Retry/idempotency rules prevent duplicate platform-side execution and unsafe write retries.
10. Static checks, unit/integration tests, migration drift, Docker/Compose and non-root checks pass.
