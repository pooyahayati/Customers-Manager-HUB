# ADR-014 — Canonical Tool Runtime and Structured Agent Tool Loop

Status: Accepted

## Context

Milestone 8 must let AI agents use live business capabilities without making a model-generated tool request an authorization boundary, without exposing raw credentials, and without coupling the conversation core to OpenAI/Gemini-specific native tool-call payloads.

The platform also needs a generic REST path, constrained live product/business lookups, and a Google Sheets path while preserving tenant isolation, retry safety, auditability, and future Policy Engine extensibility.

## Decision

### Canonical application-owned tool runtime

All tools are tenant-owned definitions executed through a provider-independent `ToolRuntime` and adapter registry.

The runtime owns:

- schema validation;
- tenant and agent authorization;
- risk/approval checks;
- credential resolution;
- retry/idempotency rules;
- adapter invocation;
- output validation;
- durable execution trace/audit.

A model request is never sufficient authorization.

### Explicit agent allowlist

Tool availability is modeled by an explicit tenant-scoped agent/tool permission relation. No permission row means deny.

### Structured agent tool loop

For agents with tools, customer-response generation uses the existing AI Gateway structured-output capability to return a canonical application schema representing either a final answer or a tool request. The runtime validates that structure, executes only an allowed tool, injects a sanitized structured result into controlled context, and repeats within a bounded loop.

This keeps provider-native tool-call formats behind the AI provider boundary and avoids spreading OpenAI/Gemini tool payload semantics into the agent runtime.

### Fixed external destinations

Generic REST and business-reference destinations are administrator-configured fixed HTTPS endpoints. The model cannot choose or override URL, host, path, authentication header, or redirect target.

The runtime performs SSRF-oriented validation, disables redirects, and resolves credentials only immediately before I/O.

Google Sheets uses a dedicated read-only adapter path with a fixed Google API host and configured spreadsheet ID.

### Encrypted credential binding

Tool credential material is encrypted with the deployment encryption key using authenticated encryption and tenant/tool-bound associated data. API responses expose credential metadata only.

### Approval safety floor

High/critical tools and tools explicitly configured as approval-required cannot execute autonomously. M8 records pending approval and Owner/Admin decisions. Operator queueing and resumable human approval orchestration remain for M11/M13.

### Retry safety

Tool executions have stable tenant-scoped idempotency keys. Successful execution results are reused on duplicate processing. Read operations may retry transient failures. Mutating REST operations may retry only when an idempotency header is configured; otherwise they make at most one external attempt.

## Alternatives considered

### Use provider-native tool calls directly in Agent Runtime

Rejected because it would couple the core runtime to provider-specific request/response formats and complicate consistent validation/fallback behavior.

### Let the model supply arbitrary URLs or HTTP request templates

Rejected because it materially expands SSRF, credential exfiltration, and authorization risk.

### Put tool credentials in prompt/tool metadata

Rejected because models must never receive raw credentials.

### Defer all approval handling to the future Policy Engine

Rejected because high-risk autonomous execution needs a deterministic safety floor before M13 exists.

### Build a separate tool microservice now

Rejected as unnecessary infrastructure. The modular monolith plus independently runnable worker architecture already provides the needed trust and execution boundary.

## Consequences

Positive:

- provider-neutral agent/tool orchestration;
- deterministic authorization independent from model behavior;
- explicit tenant/agent isolation;
- credentials stay server-side;
- constrained external network surface;
- reusable trace/idempotency foundation for later policy, analytics, and human approval features.

Trade-offs:

- structured tool loops may use an additional model round trip after each tool result;
- M8 approval records do not yet provide a full operator resume queue;
- fixed REST endpoints are less flexible than arbitrary model-generated requests, deliberately in exchange for security and auditability;
- advanced production egress controls remain part of M15 hardening.

## Follow-up implications

- M9 can compose RAG results beside tool results without changing the tool authorization boundary.
- M11 can attach pending tool approvals to the operator inbox and resume execution safely.
- M13 can add deterministic policy rules that only tighten the M8 approval floor.
- M14 can aggregate tool execution metrics from durable traces.
- M15 should add deployment-grade egress/SSRF hardening and load/security testing around external tool execution.
