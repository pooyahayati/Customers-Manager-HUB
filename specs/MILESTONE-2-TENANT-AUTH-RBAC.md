# Milestone 2 — Tenant, Authentication & RBAC Foundation

## Status

Approved execution specification.

## Goal

Establish the first production-oriented security and multi-tenant domain boundary before customer, conversation, channel, or AI features are implemented.

At completion, an authenticated platform user must be able to access only tenants for which they have an active membership, and cross-tenant access must fail deterministically at the backend.

## Architecture constraints

- Preserve the modular monolith.
- PostgreSQL remains the source of truth for users, tenants, memberships, sessions, and audit records.
- Follow ADR-004 multi-tenant isolation rules.
- Follow ADR-008 opaque server-side session strategy.
- Do not implement product-domain modules from later milestones.
- Do not add a custom permission builder, external IdP dependency, JWT infrastructure, Redis session source of truth, or microservice boundary.

## Domain model

Implement only the entities required now.

### PlatformUser

Global platform identity.

Required data:

- UUID primary key.
- Normalized unique email.
- Password hash.
- Active/disabled state.
- Created/updated timestamps.

Do not store plaintext passwords.

### Tenant

Top-level ownership/security boundary.

Required data:

- UUID primary key.
- Human-readable name.
- Stable unique slug.
- Active state.
- Created/updated timestamps.

### TenantMembership

Joins a platform user to a tenant.

Required data:

- UUID primary key.
- Tenant ID.
- User ID.
- Fixed role.
- Active state.
- Created/updated timestamps.

A user must not have duplicate active membership rows for the same tenant.

Supported roles:

- owner
- admin
- supervisor
- agent
- analyst
- viewer

### AuthSession

Server-side opaque login session.

Required data:

- UUID primary key.
- User ID.
- SHA-256 token digest with uniqueness constraint/index.
- Creation timestamp.
- Expiry timestamp.
- Optional revocation timestamp.
- Last-used timestamp only if it is used by the implemented lifecycle.

Raw session tokens must never be persisted.

### AuditEvent

Minimal privileged-change audit foundation.

Required data:

- UUID primary key.
- Tenant ID where applicable.
- Actor user ID where applicable.
- Action identifier.
- Resource type.
- Resource identifier where applicable.
- Timestamp.
- Small structured metadata only when required by the audited action.

Do not create a generic event-sourcing system.

## Persistence

Introduce SQLAlchemy 2.x and Alembic because Milestone 2 requires durable relational domain state and reproducible schema migrations.

Requirements:

- Async application database access using the existing Psycopg driver.
- One central engine/session factory.
- Explicit transaction boundaries.
- Alembic migration for only the Milestone 2 tables/indexes/constraints.
- No product tables from later milestones.
- No implicit migration execution on application import/startup.

## Authentication

Implement email/password login for the foundation.

Requirements:

- Normalize emails consistently before lookup/storage.
- Hash passwords with Argon2id.
- Login returns the generic unauthorized response for invalid email or password.
- Create a cryptographically random opaque session token.
- Persist only its SHA-256 digest.
- Set the raw session token in an HTTP-only cookie.
- Logout revokes the current server-side session and clears the cookie.
- Expired/revoked sessions are rejected.
- Disabled users cannot authenticate or continue using sessions.

Initial endpoints:

- `POST /auth/login`
- `POST /auth/logout`
- `GET /auth/me`

Do not implement registration, password reset, email verification, MFA, OAuth, or SSO in this milestone.

## Tenant APIs and authorization

Implement the minimum API surface needed to prove tenant isolation.

Initial endpoints:

- `GET /tenants` — list tenants available to the authenticated user.
- `GET /tenants/{tenant_id}` — return tenant only when membership permits access.

Privileged bootstrap/creation may be exercised through migration/test fixtures rather than exposing public tenant-registration endpoints during this milestone.

Every tenant-scoped endpoint must resolve authentication and membership server-side before loading tenant-owned resources.

## RBAC policy

Use fixed server-side role-to-permission policy definitions.

Initial permissions should be limited to what this milestone exercises, such as:

- tenant.read
- tenant.manage
- membership.read
- membership.manage
- audit.read

Do not model unused future permissions.

The implementation must make permission checks reusable for later tenant-owned modules without creating an abstract policy framework.

## Audit

Record audit events for privileged configuration mutations implemented in this milestone.

Authentication failures must not write sensitive payloads or plaintext credentials to audit/log output.

## Security

Mandatory:

- Tenant isolation enforced server-side.
- Session token digest only in DB.
- HTTP-only cookie.
- Secure cookie outside local development.
- SameSite=Lax default.
- Finite session lifetime from typed configuration.
- Password hashes only.
- Generic login failure response.
- No secrets in structured logs.
- Foreign keys and uniqueness constraints enforce core invariants.

## Configuration

Add only settings actively required by the implementation, expected to include:

- Session cookie name.
- Session lifetime.
- Cookie secure behavior derived from environment where practical.

Do not add future SSO/OAuth configuration.

## Tests

Required fast tests:

- Email normalization.
- Password hash/verify behavior.
- Session token digest behavior.
- Role/permission policy behavior.
- Authentication rejects bad credentials generically.
- Revoked/expired session rejection.
- Disabled-user session rejection.
- Authenticated `/auth/me` success.
- User tenant listing contains only memberships.
- Cross-tenant access negative test.

Required PostgreSQL integration tests/smoke:

- Alembic upgrade from empty database succeeds.
- Required constraints/indexes exist through behavior.
- Tenant A user cannot read Tenant B through API.

No paid/external service calls.

## Out of scope

- Contacts/conversations/messages.
- Channel accounts.
- Telegram/Website Chat.
- AI providers or agents.
- Custom roles/permission editor.
- Organization hierarchy beyond Tenant.
- Invitations/onboarding workflow.
- Password recovery/MFA/SSO.
- Billing/subscriptions.
- Redis-backed sessions.

## Acceptance gate

Milestone 2 is acceptable when:

1. Dependency lock is reproducible.
2. Ruff format/lint, Pyright, and Pytest pass.
3. Alembic migration upgrades a clean PostgreSQL database.
4. API + Worker + Web + PostgreSQL + Redis still start successfully through Compose.
5. A seeded test user can log in and resolve `/auth/me`.
6. The user can list only assigned tenants.
7. Explicit cross-tenant access receives an authorization/not-found response without data leakage.
8. Session revocation is effective immediately.
9. No real credentials or temporary CI files remain in the final diff.
