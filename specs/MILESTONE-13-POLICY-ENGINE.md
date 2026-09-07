# Milestone 13 — Policy Engine

## Status

Implementation specification for M13.

## Goal

Move critical business restrictions out of prompt-only enforcement and into a deterministic, tenant-scoped application policy layer.

The Policy Engine is an enforcement boundary. Model output may request an action, but model output never authorizes the action.

## Scope

M13 implements:

- tenant policy configuration and revision tracking;
- deterministic message-type policy overlays;
- AI autonomy policy;
- business-hours behavior;
- deterministic human-handoff triggers;
- per-tool allow/deny policy overlays;
- configurable approval strengthening for tool calls;
- application-side policy decision traces;
- admin APIs for policy configuration and trace inspection;
- integration with the channel worker, Tool Runtime, and existing handoff foundation.

## Non-goals

M13 does not introduce:

- a general-purpose rules DSL;
- user-authored executable expressions;
- a workflow builder;
- a separate policy microservice;
- policy decisions delegated to an AI model;
- platform-global policy administration;
- billing/quota enforcement;
- advanced calendar/holiday scheduling.

## Design principles

1. Policy is deterministic application code, not prompt prose.
2. Policy may only preserve or tighten existing security boundaries.
3. A policy `allow` never bypasses tenant isolation, RBAC, channel capability, agent-tool permission, schema validation, SSRF controls, credential controls, or built-in high-risk approval requirements.
4. Policy defaults preserve M0–M12 behavior.
5. Policy configuration is tenant-scoped and auditable.
6. Policy evaluation emits append-oriented safe traces without storing message bodies, credentials, tool arguments, or other unnecessary sensitive payloads.
7. Existing M11 handoff configuration remains backward compatible.

## Domain model

### TenantPolicy

One optional policy row per tenant. Absence means deterministic defaults.

Fields:

- `tenant_id`
- `enabled`
- `revision`
- `timezone`
- `business_hours`
- `outside_business_hours_action`
- `autonomy_mode`
- `message_rules`
- `handoff_keywords`
- `handoff_on_tool_approval`
- `approval_min_risk`
- `require_approval_for_writes`
- timestamps

### ToolPolicyRule

Optional per-tool overlay:

- `tenant_id`
- `tool_id`
- `effect`: `allow | deny`
- `approval_mode`: `inherit | required`
- timestamps

An `allow` rule only means the policy layer does not deny the tool. Agent permission is still required independently.

### PolicyDecisionTrace

Append-oriented record of a material policy evaluation:

- tenant;
- policy revision;
- decision type;
- action;
- reason code;
- optional conversation/message/agent/tool references;
- bounded safe metadata;
- creation timestamp.

No raw credentials, full message text, tool arguments, or model prompts are stored in policy traces.

## Policy enums

### Autonomy mode

- `autonomous` — autonomous customer responses are permitted subject to all other gates.
- `assist_only` — autonomous customer responses are paused and the conversation is routed to human handoff; operator AI assist remains available through the M11 flow.
- `human_only` — autonomous customer responses are paused and the conversation is routed to human handoff.

### Message action

- `allow`
- `deny`
- `handoff`

Message policy is an overlay. It cannot enable a content type disabled by the channel capability configuration.

### Outside-business-hours action

- `allow`
- `handoff`
- `human_only`

### Tool effect

- `allow`
- `deny`

### Tool approval mode

- `inherit`
- `required`

## Business hours

Business hours are configured as a weekly schedule in the tenant policy timezone.

Canonical JSON shape:

```json
{
  "mon": [{"start": "09:00", "end": "17:00"}],
  "tue": [{"start": "09:00", "end": "17:00"}]
}
```

Rules:

- timezone must be a valid IANA timezone available to the runtime;
- weekdays are `mon` through `sun`;
- windows are half-open `[start, end)`;
- `end` must be after `start`;
- windows on one day must not overlap;
- overnight ranges must be split across adjacent days;
- an empty schedule means business-hours restrictions are not active.

## Tool policy evaluation

Tool execution keeps the M8 security chain:

1. tenant-scoped active Agent exists;
2. Agent has explicit `AgentToolPermission`;
3. tool is active;
4. input schema validates;
5. policy evaluation runs;
6. credentials resolve server-side;
7. adapter execution runs;
8. output schema validates.

Policy decision:

- explicit tool `deny` => execution is deterministically denied;
- otherwise approval is required when any of these are true:
  - the Tool definition requires approval;
  - built-in risk is `high` or `critical`;
  - tenant `approval_min_risk` requires approval at a stricter threshold;
  - tenant requires approval for write operations;
  - per-tool policy explicitly requires approval.

Policy never removes a built-in approval requirement.

## Message and autonomy flow

After canonical channel persistence and before autonomous AI execution:

1. channel capability remains authoritative;
2. Policy Engine evaluates the persisted message type;
3. `deny` marks the event ignored and no transcription/AI execution occurs;
4. `handoff` routes the conversation into M11 human handoff;
5. allowed voice messages may be transcribed normally;
6. Policy Engine evaluates business hours, autonomy mode, and handoff keywords;
7. any deterministic handoff decision creates/reuses the active M11 handoff;
8. only an `allow` decision reaches autonomous Agent Runtime.

## Handoff compatibility

The existing `HandoffPolicy` API remains supported.

M13 behavior:

- migration copies existing M11 handoff policy values into `TenantPolicy` where present;
- new Policy API writes the compatibility fields back to `HandoffPolicy`;
- the legacy M11 handoff-policy API writes the equivalent fields into `TenantPolicy`;
- runtime prefers the M13 policy when available and falls back to M11 defaults/legacy data otherwise.

## API surface

Tenant-scoped admin API:

- `GET /api/v1/tenants/{tenant_id}/policies`
- `PUT /api/v1/tenants/{tenant_id}/policies`
- `GET /api/v1/tenants/{tenant_id}/policies/tool-rules`
- `PUT /api/v1/tenants/{tenant_id}/policies/tool-rules/{tool_id}`
- `DELETE /api/v1/tenants/{tenant_id}/policies/tool-rules/{tool_id}`
- `GET /api/v1/tenants/{tenant_id}/policies/traces`

Policy mutation requires Owner/Admin. Trace/read access follows authenticated tenant membership.

## Audit

Configuration mutations write `AuditEvent` records.

Runtime evaluations write `PolicyDecisionTrace` rows containing only bounded safe metadata.

## Idempotency and failure behavior

- Policy evaluation is read-only except for trace insertion.
- Trace failure must not silently convert a deny/handoff decision into allow.
- Existing tool execution idempotency remains authoritative.
- Existing active-handoff uniqueness remains authoritative.
- Worker retry must not create duplicate customer responses.

## Security invariants

- All policy reads/writes are tenant-scoped.
- Cross-tenant tool rules are rejected.
- `allow` cannot bypass AgentToolPermission.
- `allow` cannot bypass disabled channel capabilities.
- `approval_min_risk` cannot weaken the built-in high/critical approval floor.
- Policy evaluation does not receive decrypted credentials.
- Policy traces exclude full customer text and tool arguments.
- Human-active state remains an independent hard stop for autonomous AI.

## Validation

M13 acceptance validation must include:

- Ruff and formatter;
- Pyright strict;
- migration upgrade and Alembic drift check;
- policy CRUD/RBAC/tenant-isolation tests;
- business-hours deterministic evaluation tests;
- message allow/deny/handoff tests;
- autonomy and handoff trigger tests;
- tool deny and approval-strengthening tests;
- proof that policy allow does not bypass AgentToolPermission;
- full backend unit/integration regression;
- frontend regression;
- Docker/Compose build, migration, readiness, and non-root checks.

## Exit gate

Critical business permissions and autonomy restrictions are enforced application-side regardless of model output.