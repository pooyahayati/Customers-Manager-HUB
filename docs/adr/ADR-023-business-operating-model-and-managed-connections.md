# ADR-023: Business operating model and managed connections

- Status: Accepted for staged implementation
- Date: 2026-09-10

## Context

The platform already separates tenants, agents, prompts, knowledge, tools, channels, policies, conversations, and human handoff. Ordinary Business users nevertheless need a coherent setup path. Business identity is otherwise scattered across prompt text, tenant naming, policies, and uploaded documents, while credentials risk becoming fragmented across channel-, tool-, and source-specific screens.

The product also needs to distinguish relatively stable knowledge suitable for indexing from volatile transactional truth that must be read through authoritative live APIs.

## Decision

### Business Profile is a first-class tenant-scoped domain

The platform will add a structured Business Profile and a derived Setup Readiness view. Business Profile data is not a replacement for knowledge, policies, Agent instructions, or customer memory.

The default setup sequence is:

`Business Profile → Connections → Knowledge → Agent → Rules → Test → Channel → Operations`

### Connections use a canonical domain contract

External authentication relationships will be represented through a canonical tenant-scoped connection contract with provider adapters. Initial adapters cover WordPress and Google capabilities, including Drive, Sheets, and YouTube where authorized.

Core Business logic must not consume provider token formats directly. Raw secrets remain in encrypted server-side credential bindings and are never provided to models or browser clients.

Google Drive, Google Sheets, and YouTube remain distinct capabilities even when they share one Google OAuth relationship.

### Stable knowledge and live truth remain separate

- Stable or slowly changing content is ingested as versioned knowledge with provenance and tenant-scoped vector retrieval.
- Volatile data such as price, inventory, and order status is accessed through an authorized live tool with explicit schema, timeout, caching, host restrictions, rate limits, and audit.
- A source may offer both scheduled ingestion and live lookup, but the runtime contracts and traces remain distinct.

### Initial knowledge access defaults to Business-wide

Every active Agent initially receives access to every ready knowledge base in the same Business. The backend retains an explicit authorization boundary so selective access can be enabled later. Cross-tenant retrieval remains forbidden.

### Configuration assistance is draft-only

A later Configuration Assistant will conduct a bounded guided interview and produce structured configuration and prompt drafts. Owner-managed prompt versions, deterministic guardrails, model routing, cost, and limits govern it. It cannot publish changes, request credentials, or bypass platform and Business policy.

## Consequences

### Positive

- Ordinary users receive one understandable setup journey.
- Business facts, AI behavior, enforcement, and external capabilities remain distinct.
- Provider integrations can grow without coupling core logic to provider SDKs.
- Knowledge freshness and live-data freshness can be explained and audited correctly.
- The future Configuration Assistant has structured inputs and safe output boundaries.

### Costs and risks

- Business Profile and Connection lifecycle require new schemas, migrations, RBAC, audit, and negative tenant-isolation tests.
- Shared Google authorization requires careful capability/scope consent and revocation behavior.
- Default Business-wide knowledge access is intentionally simple but unsuitable for every future multi-brand or sensitive-data case; the retained authorization boundary is mandatory.
- Live APIs require SSRF protection, bounded caching, response validation, and provider-aware rate limiting.

## Rejected alternatives

### Store Business understanding only in a system prompt

Rejected because it is difficult to validate, reuse, audit, localize, and safely update.

### Treat all external information as vector knowledge

Rejected because indexed price, inventory, and order state can become stale and should come from authoritative live systems.

### Give each connector an unrelated credential implementation

Rejected because it duplicates security-sensitive lifecycle logic and makes health, revocation, audit, and future provider support inconsistent.

### Let the Configuration Assistant directly publish changes

Rejected because model output is untrusted and configuration changes require explicit human review.

## Implementation reference

The staged module behavior, UI responsibilities, ingestion lifecycle, implementation packages, and quality gates are defined in `docs/specs/BUSINESS-OPERATING-MODEL-V1.md`.
