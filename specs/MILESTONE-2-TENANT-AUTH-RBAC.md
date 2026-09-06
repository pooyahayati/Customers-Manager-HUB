# Milestone 2 — Tenant, Authentication & RBAC Foundation

## Goal

Establish the first real product security boundary: authenticated platform users, isolated tenant memberships, fixed initial tenant roles, revocable Admin Console sessions, and auditable privileged tenant changes.

This milestone must prove tenant isolation with real PostgreSQL-backed integration tests before later tenant-owned domains are implemented.

## Architecture

ADR-008 is authoritative for Admin authentication and tenant context.

Use:

- PostgreSQL as the source of truth.
- SQLAlchemy 2.x for typed persistence and transactions.
- Alembic for explicit schema migrations.
- Psycopg through the existing PostgreSQL dependency.
- Argon2 password hashing through `pwdlib`.
- Opaque server-side sessions stored as token hashes.

Do not add Redis-backed identity/session state, JWT bearer auth, OAuth/OIDC, or an external identity provider in this slice.

## Data model

Implement only these persisted concepts:

### Tenant

- UUID primary key.
- Unique normalized slug.
- Display name.
- Active status.
- Created/updated timestamps.

### PlatformUser

- UUID primary key.
- Unique normalized email.
- Password hash.
- Active status.
- Created/updated timestamps.

### TenantMembership

- Tenant ID.
- User ID.
- Role: `owner`, `admin`, `supervisor`, `agent`, `analyst`, `viewer`.
- Active status.
- Created/updated timestamps.
- Unique `(tenant_id, user_id)` membership.

### AuthSession

- UUID primary key.
- User ID.
- Unique SHA-256 token hash.
- Created timestamp.
- Expiry timestamp.
- Revoked timestamp nullable.

Do not persist raw session tokens.

### AuditEvent

- UUID primary key.
- Tenant ID nullable for future platform-level events.
- Actor user ID nullable when a system actor is legitimate.
- Action.
- Target type and target ID.
- Small JSON metadata object.
- Created timestamp.

Audit metadata must not contain secrets.

## RBAC

Initial role permissions are fixed in server-side code.

For this milestone:

- `owner` and `admin` may update tenant profile settings.
- Other roles may read the tenant but may not perform privileged tenant changes.
- Every tenant-scoped route resolves membership server-side.

The permission mapping should be small and explicit; do not create a generic policy DSL or database permission graph yet.

## Bootstrap

Provide a local/self-hosted administrative bootstrap path that can create the initial tenant, owner user, and owner membership only when the requested identities do not already conflict.

Prefer an explicit CLI using interactive password input. The reusable bootstrap function must be independently testable.

Do not expose unauthenticated HTTP registration/bootstrap.

## API scope

Implement only the endpoints required to prove the boundary:

```text
POST /api/v1/auth/login
POST /api/v1/auth/logout
GET  /api/v1/auth/me
GET  /api/v1/tenants
GET  /api/v1/tenants/{tenant_id}
PATCH /api/v1/tenants/{tenant_id}
```

Semantics:

- Login accepts email/password, returns authenticated user summary, and sets the session cookie.
- Login failure is generic and does not disclose whether an email exists.
- Logout revokes the current session and clears the cookie.
- `/auth/me` returns the current user or 401.
- `/tenants` returns only active tenant memberships for the current user.
- Tenant detail requires active membership.
- Tenant update requires `owner` or `admin` and writes an audit event in the same transaction.

## Session security

- Generate session tokens with Python `secrets` using at least 256 bits of entropy.
- Store only SHA-256 token hashes.
- Use constant-time-safe library behavior for password verification.
- Session cookies: `HttpOnly`, `SameSite=Strict`, path `/`.
- `Secure=true` outside development/test.
- Default session TTL: 7 days, configurable only through typed settings if needed immediately.
- Revoked/expired sessions authenticate as 401.
- Inactive users or inactive memberships cannot authorize tenant access.

## Database/migrations

Introduce Alembic now because persistent product entities begin in this milestone.

Rules:

- One initial migration for Milestone 2 tables/indexes/constraints.
- Migrations never run as an import side effect.
- Application startup does not silently migrate the schema.
- Add one concise operational command/Make target for explicit migration.

## Tests

Required automated coverage:

- Password hash/verify behavior.
- Session token hashing does not persist the raw token.
- Successful login sets the expected cookie attributes.
- Invalid login returns generic 401.
- Revoked/expired session returns 401.
- User sees only own tenant memberships.
- Cross-tenant tenant-detail access returns 403 or 404 consistently without leaking data.
- Viewer/analyst/etc. cannot update tenant profile.
- Owner/admin can update tenant profile.
- Privileged update writes an audit event transactionally.
- Inactive membership cannot authorize.
- Migration upgrade works against PostgreSQL.

Use real PostgreSQL integration tests for tenant isolation and persistence behavior. No SQLite substitute for the acceptance gate.

## Explicitly out of scope

- Public signup.
- Password reset/email delivery.
- Email verification.
- Invitations/user-management UI.
- MFA/passkeys.
- OAuth/OIDC/SSO.
- API keys/service accounts.
- Custom roles/permissions editor.
- Platform-owner administration APIs.
- Tenant deletion/billing/subscriptions.
- Product-domain entities beyond the five concepts above.
- Admin Console login UI in this backend-focused slice.

## Acceptance gate

The slice is acceptable only when:

```text
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
alembic upgrade head
```

and PostgreSQL-backed integration validation proves:

1. Initial owner/tenant can be bootstrapped.
2. Owner can log in and receive a server-side session cookie.
3. `/auth/me` resolves the session.
4. Tenant listing is membership-scoped.
5. Cross-tenant access is denied.
6. Viewer cannot update tenant settings.
7. Owner/admin can update tenant settings and an audit event is created.
8. Logout immediately revokes the session.

No real secrets or temporary validation workflows remain in the final diff.
