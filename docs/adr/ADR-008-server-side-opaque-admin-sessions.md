# ADR-008 — Server-Side Opaque Sessions for Admin Authentication

## Status

Accepted

## Context

Milestone 2 introduces the first authenticated, tenant-scoped platform operations. The Admin Console needs authentication that is revocable, auditable, compatible with multi-tenant authorization, and simple enough for the current modular-monolith architecture.

Stateless JWT access tokens would move session validity into bearer-token lifetime and require additional revocation/rotation machinery before it provides a practical advantage. The current product does not require independently validating access tokens across separate services.

## Decision

Use **opaque server-side sessions** for Admin Console authentication.

### Session model

- Generate a cryptographically random session token at login.
- Return the raw token only to the authenticated client.
- Persist only a one-way SHA-256 digest of the token in PostgreSQL.
- Store session ownership, creation time, expiry time, revocation time, and last-use metadata required by the implemented lifecycle.
- Session validity is checked server-side on authenticated requests.
- Logout/revocation takes effect immediately in the database.

### Browser transport

The Admin Console authentication contract will use a secure HTTP-only cookie when browser login is implemented.

Production cookie defaults must be:

- `HttpOnly` enabled.
- `Secure` enabled outside local development.
- `SameSite=Lax` unless a concrete deployment requirement justifies another value.
- Explicit finite lifetime matching the server-side session expiry.

Authentication secrets must not be stored in browser local storage.

### Password authentication

For the initial self-hosted/SaaS foundation, platform users may authenticate with normalized email plus password.

- Passwords are stored only as modern password hashes using Argon2id.
- Password hashing parameters remain centralized in the authentication module.
- Plaintext passwords are never logged or persisted.
- External identity providers/SSO may be added later behind the same application-level identity/session boundary.

### Tenant authorization

Authentication and tenant selection are separate concerns.

- A user may have memberships in multiple tenants.
- Tenant-owned API routes carry an explicit `tenant_id` in their server-side contract rather than relying on frontend filtering.
- Every tenant-scoped operation verifies an active membership before accessing tenant-owned data.
- Cross-tenant access is denied by default.

### RBAC

The MVP uses the fixed product roles already defined by the platform contract:

- Owner
- Admin
- Supervisor
- Agent
- Analyst
- Viewer

The role-to-permission mapping is deterministic server-side policy code. A database-driven custom permission builder is deferred until a real product requirement justifies it.

## Alternatives Considered

### Stateless JWT access tokens

Rejected for the initial Admin Console because immediate revocation, session management, and auditability would require additional machinery without providing a current architectural benefit.

### Third-party identity provider as a hard dependency

Rejected for the foundation because the platform must remain self-hostable and should not require an external SaaS identity service to boot.

### Database-configurable custom roles from day one

Rejected as premature complexity. Fixed roles satisfy the documented MVP authorization model while preserving a later migration path.

## Consequences

Positive:

- Immediate session revocation.
- Clear server-side tenant authorization boundary.
- No JWT signing-key lifecycle in the MVP.
- Compatible with self-hosted deployment.
- Simple audit correlation between user, session, tenant, and privileged action.

Trade-offs:

- Authenticated requests require a session lookup.
- PostgreSQL availability is required for session validation.
- Horizontal scaling relies on the shared database, which already exists as platform infrastructure.

## Security Requirements

- Session tokens must have sufficient cryptographic entropy.
- Only token digests may be persisted.
- Session lookup must not leak whether another tenant/user session exists.
- Login errors should not disclose whether an email address is registered.
- Tenant authorization must be enforced in backend dependencies/policies and tested with cross-tenant negative cases.
- Privileged tenant changes introduced in Milestone 2 must produce audit records.
