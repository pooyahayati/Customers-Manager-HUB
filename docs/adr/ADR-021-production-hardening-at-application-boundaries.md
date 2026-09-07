# ADR-021 — Production hardening at application boundaries

## Status

Accepted for Milestone 15.

## Context

The MVP already has durable PostgreSQL state, Redis Streams workers, S3-compatible storage, structured credentials, tenant isolation, policy enforcement, health checks and SSRF defenses. Production hardening must close operational gaps without changing the modular-monolith topology before scale data justifies it.

## Decision

1. Keep PostgreSQL, Redis Streams and S3-compatible object storage as the supported production topology.
2. Enforce public-ingress rate limiting in the API with Redis so limits are consistent across API replicas. Edge rate limiting remains defense in depth, not the sole enforcement point.
3. Keep Generic REST SSRF enforcement inside Tool Runtime: fixed HTTPS destinations, DNS resolution to globally routable addresses and redirects disabled.
4. Add finite delivery accounting and dead-letter streams to the existing Redis Stream queues instead of introducing another broker.
5. Improve observability through correlation IDs and structured logs rather than adding a metrics backend in the MVP.
6. Keep backup/restore tooling topology-specific and simple: PostgreSQL logical backups plus infrastructure snapshots for Redis/object storage.
7. Add repository security scanning as a separate GitHub Actions workflow so security failures are visible independently from functional CI.
8. Do not automatically delete durable customer/business data until tenant/legal retention policy exists. M15 establishes the documented retention classes and operational deletion boundary only.

## Consequences

- No new runtime service is required.
- Redis remains a required dependency for queues and distributed rate limiting.
- Retry exhaustion becomes inspectable instead of producing permanent pending entries.
- Request-level troubleshooting improves without logging sensitive payloads.
- Restore/upgrade procedures are reproducible for the Compose topology.
- Future managed deployments can replace Redis rate limiting, monitoring or backup mechanisms behind the same operational contracts.