# ADR-008 — Server-Side Admin Sessions and Explicit Tenant Context

## Status

Accepted

## Context

Milestone 2 introduces authenticated platform users, tenant memberships, RBAC, and privileged tenant configuration. The Admin Console needs revocable authentication that works for both self-hosted and future SaaS deployments without coupling authorization to frontend state.

The platform is multi-tenant by design, so authentication alone must never imply access to a tenant. Every tenant-scoped operation must resolve and authorize tenant membership on the server.

## Decision

### Authentication

Use opaque, random, server-side sessions for the Admin Console.

- A successful login creates a cryptographically random session token.
- Only a SHA-256 hash of the token is stored in PostgreSQL.
- The raw token is returned only in an `HttpOnly` session cookie.
- Cookies use `SameSite=Strict`; `Secure` is mandatory outside development/test.
- Sessions have an explicit expiry and can be revoked immediately.
- Logout revokes the server-side session and clears the cookie.
- Passwords are hashed with Argon2 through `pwdlib`; plaintext passwords are never persisted or logged.

Session records identify a platform user, not a permanently selected tenant.

### Tenant context

Tenant-scoped API routes carry the tenant identifier explicitly in the route, for example:

```text
/api/v1/tenants/{tenant_id}/...
```

The backend resolves the authenticated user, loads the matching tenant membership, and rejects requests without an active authorized membership. Frontend filtering is never an authorization boundary.

### RBAC

The initial tenant roles are fixed product roles:

- Owner
- Admin
- Supervisor
- Agent
- Analyst
- Viewer

Permissions are defined server-side in application policy code. A custom role/permission builder is deferred until product requirements justify it.

### Bootstrap

Do not expose an unauthenticated HTTP "first user wins" bootstrap endpoint.

Initial self-hosted tenant/owner provisioning is performed through an explicit administrative CLI command/function against the database. Interactive password input is preferred so credentials do not need to appear in shell history.

### Audit

Privileged tenant configuration changes create append-only audit records with actor, tenant, action, target, timestamp, and bounded metadata. Audit records must not contain passwords, raw session tokens, or other secrets.

## Alternatives Considered

### Stateless JWT access tokens as the primary Admin Console session

Rejected for the initial product because revocation, session inventory, logout semantics, and tenant access changes become more complex without providing a current benefit. JWT/OIDC can still be added later for external identity-provider integration.

### Store sessions only in Redis

Rejected as the initial source of truth. PostgreSQL provides durable, auditable session state and immediate revocation without making Redis persistent identity state.

### Store the active tenant in the session

Rejected as the authorization boundary because users may belong to multiple tenants and stale client/session selection must not grant access. Tenant authorization is checked per tenant-scoped request.

### Public registration/bootstrap endpoint

Rejected because an uninitialized deployment could be claimed by an unintended caller.

## Consequences

Positive:

- Immediate session revocation and clear logout semantics.
- Tenant isolation remains explicit and testable.
- No JWT key lifecycle or token-claim synchronization is required for the initial Admin Console.
- Self-hosted deployment does not depend on an external identity provider.
- Future OIDC/SSO can map into the same platform-user and tenant-membership model.

Trade-offs:

- Authenticated requests require a session lookup.
- PostgreSQL availability is required for Admin authentication.
- Cross-origin Admin deployments must deliberately configure cookie/CORS behavior rather than relying on bearer tokens by default.

## Follow-up Implications

- Milestone 2 requires PostgreSQL migration infrastructure.
- Login rate limiting and broader production hardening remain required before public production exposure.
- MFA, password reset, email verification, invitations, SSO/OIDC, custom roles, and API/service tokens are separate later capabilities.
- Customer Website Chat authentication is not covered by this ADR and remains a separate public-client security design under ADR-007.
