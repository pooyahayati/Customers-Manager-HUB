# ADR-024 - Linux Production Compose and Caddy Ingress

## Status

Accepted.

## Context

The supported single-server deployment needs a repeatable production path for an operator who
already has Docker Engine and Docker Compose. The base Compose file intentionally exposes local
development ports and does not terminate TLS. Reusing it unchanged on an Internet-facing host
would expose application and infrastructure services directly and would leave secret generation,
migrations, and the first Platform Owner as manual, failure-prone steps.

ADR-021 did not require another application service for production hardening. This decision adds a
deployment ingress only; it does not change the modular-monolith, worker, or domain topology.

## Decision

- Add `compose.production.yaml` as an overlay on `compose.yaml`.
- Caddy is the only service that publishes host ports: TCP 80, TCP 443, and UDP 443 for HTTP/3.
- API, Web, PostgreSQL, Redis, and S3-compatible object storage remain reachable only on the
  production Compose network. Caddy routes `/api/*`, `/health`, and `/ready` to API and all other
  requests to Web.
- Give Caddy a fixed address on a deployment-specific subnet. API trusts only that address for
  forwarded client IPs.
- Use Caddy automatic HTTPS with a required public hostname and ACME contact email. Certificate
  state is persisted in dedicated volumes.
- Keep the reference object storage unexposed on the same Docker host. Its HTTP endpoint is allowed
  in production only when an explicit setting is enabled and the hostname is exactly
  `object-storage`. External or multi-host object-storage endpoints still require HTTPS.
- Generate deployment secrets with `umask 077`, store them in an ignored mode-0600 environment
  file, and never overwrite an existing environment file.
- Use the existing Alembic and application bootstrap CLIs. Bootstrap is skipped only when the
  configured active Platform Owner already exists; a non-empty conflicting identity database fails
  closed.
- The installer is idempotent only for the recorded source revision. A source revision change
  requires the documented backup-first upgrade procedure.
- The installer checks prerequisites but never installs Docker or removes volumes.

## Alternatives considered

### Publish API and Web directly and rely on a host firewall

Rejected because Compose would still create direct host bindings and TLS configuration would be an
undocumented external dependency.

### Put every service behind Caddy routes

Rejected because PostgreSQL, Redis, and object storage have no public HTTP contract and must remain
private infrastructure.

### Automate upgrades in the first-install script

Rejected because migration-bearing upgrades require a verified backup, release review, and an
explicit rollback decision. Those steps are documented rather than hidden in an unsafe one-command
update.

### Require internal TLS between same-host containers

Deferred for the single-host reference topology. The object-storage service has no host binding,
credentials are generated per deployment, and the bridge network is deployment-specific. External
or multi-host storage remains HTTPS-only.

## Consequences

- DNS and inbound 80/443 are hard prerequisites for first installation and ACME issuance.
- Operators must avoid subnet collisions or change the subnet, Caddy IP, and trusted proxy CIDR
  together before first start.
- The environment file and Caddy data volumes become security-sensitive operational state.
- A compromised container on the same bridge can reach private services; host access and Docker
  daemon access therefore remain privileged production concerns.
- Development Compose ports and volumes are unchanged.

## Follow-up implications

- A multi-host or highly available deployment needs a separate ADR for ingress, secret management,
  managed data services, and internal transport security.
- Release validation must run Compose model checks, host-port assertions, migration drift checks,
  and public HTTPS readiness.
- Backup policy must cover PostgreSQL, Redis, object storage, the protected environment file, and
  Caddy certificate state.
