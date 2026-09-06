# ADR-004: Multi-Tenant by Design

**Status:** Accepted

## Context

Customers Manager HUB is intended for use by multiple independent businesses. Tenant data includes channels, provider credentials, prompts, tools, knowledge, contacts, conversations, memory, policies, users, usage, and audit information.

Retrofitting tenant isolation later would create a high risk of cross-customer data leakage and expensive schema/application changes.

## Decision

Multi-tenancy is a foundational architecture invariant.

Tenant-owned records must be explicitly tenant-scoped unless a documented relational invariant safely derives tenant ownership.

Tenant context must be resolved from authenticated or trusted integration context and enforced server-side.

A client-provided `tenant_id` is never sufficient authorization.

Repositories/application queries must include tenant scoping and security-sensitive paths require negative cross-tenant tests.

Tenant-aware scoping also applies to:

- vector retrieval
- cache keys
- queue jobs
- object storage paths/metadata
- tool credentials
- AI execution traces
- audit records

## Alternatives Considered

### Add tenancy after single-customer MVP

Rejected due to migration complexity and unacceptable leakage risk.

### Database-per-tenant initially

Not selected as the initial default because it increases provisioning, migration, pooling, backup, and operational complexity before customer scale requires it.

### Frontend-only workspace filtering

Rejected because it is not a security boundary.

## Consequences

Positive:

- Commercial SaaS/self-hosted multi-business architecture is supported from the start.
- Tenant leakage becomes a testable invariant.
- Modules are forced to model ownership explicitly.

Trade-offs:

- Nearly every domain operation must carry trusted tenant context.
- Developers must be disciplined about tenant-aware indexes, cache keys, jobs, and storage.

## Follow-up

- Define tenant context middleware/application abstraction.
- Define tenant-aware repository patterns.
- Evaluate PostgreSQL Row-Level Security as defense-in-depth in a later ADR.
- Define platform-owner cross-tenant operations separately from tenant-user permissions.
