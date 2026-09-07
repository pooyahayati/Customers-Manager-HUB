# Milestone 15 — Security & Production Hardening

## Status

Implementation specification for M15.

## Goal

Prepare the supported Docker Compose MVP topology for controlled production deployment without introducing a new service tier, event platform, metrics stack, or secret manager dependency.

## Scope

M15 completes the production baseline for:

- application-side rate limiting for unauthenticated/public ingress;
- request correlation and structured operational logs;
- staging/production configuration and secret guards;
- file signature/archive hardening for PDF/XLSX knowledge ingestion;
- verification of existing SSRF controls for generic REST tools;
- bounded Redis Stream retries and dead-letter streams;
- dependency, secret and container scanning in GitHub Actions;
- PostgreSQL backup/restore automation and restore smoke validation;
- documented database upgrade/rollback procedure;
- explicit data-retention policy foundation;
- health/readiness operational contract;
- lightweight load-test tooling for supported HTTP ingress;
- security and production runbooks.

## Non-goals

M15 does not add Kubernetes, a service mesh, Vault/KMS, a SIEM, Prometheus/Grafana, a data warehouse, a separate job broker, or automatic production deployment.

## Security invariants

1. Production must not start with debug mode, bootstrap database credentials, a missing/placeholder encryption key, or development object-storage credentials.
2. Public ingress rate limits are enforced through Redis and return HTTP 429 with a bounded retry hint.
3. Client IP forwarding is trusted only when the immediate peer belongs to explicitly configured proxy CIDRs.
4. Rate-limit keys contain hashes rather than raw client addresses.
5. Generic REST tools remain HTTPS-only, reject private/non-global destinations after DNS resolution, and do not follow redirects.
6. Uploaded knowledge files must satisfy the existing byte limit and additionally pass format-signature/archive safety checks before parser work.
7. Retryable Redis Stream jobs have a finite delivery budget. Exhausted or malformed jobs are moved to a dead-letter stream and acknowledged from the live stream.
8. Operational logs expose correlation and bounded metadata, not credentials, prompts, message bodies, tool payloads, memory values, or session tokens.
9. Security scanners are read-only and never receive production secrets.

## Rate limiting

Default limits are configuration-driven and apply to state-changing public ingress:

- login: 10 requests / 60 seconds / client;
- Website Chat public endpoints: 120 requests / 60 seconds / client;
- Telegram webhook endpoints: 180 requests / 60 seconds / client.

The application uses Redis for cross-instance consistency. When Redis itself is unavailable the limiter fails open but emits a structured warning; `/ready` already becomes unhealthy because Redis is a required runtime dependency. Production edge/proxy rate limiting remains recommended defense in depth.

## Retry and dead-letter contract

Channel and knowledge queues retain their existing consumer-group/reclaim model. Each stream entry has an external Redis delivery-attempt counter. Initial delivery counts as attempt 1. A pending entry reclaimed after a failed/crashed processing attempt increments the counter. Once the configured maximum would be exceeded, the entry is copied to the corresponding dead-letter stream with its entity ID, source stream ID, attempt count and a bounded reason code, then acknowledged/deleted from the live stream.

Malformed entries are dead-lettered instead of remaining permanently pending.

Default maximum delivery attempts: 5.

## File hardening

Knowledge ingestion keeps the 20 MiB object limit and existing parser bounds. M15 additionally requires:

- PDF header `%PDF-` within the first 1024 bytes;
- XLSX ZIP signature;
- no encrypted XLSX ZIP entries;
- no absolute or parent-traversal ZIP paths;
- no ZIP symlink entries;
- bounded individual expanded entry size;
- bounded compression ratio in addition to total expanded bytes/entry count.

## Observability

Every API response receives `X-Request-ID`. A bounded inbound request ID may be propagated; otherwise a random ID is generated. Request completion/failure is emitted as structured JSON with method, path, status and latency. Existing worker `extra` metadata becomes visible through an explicit formatter allowlist.

## Backup and upgrade

The supported topology is Docker Compose. PostgreSQL logical backup/restore scripts are part of the repository. CI performs a restore smoke against a temporary database. Redis AOF and object-storage volumes are covered by the operational volume-snapshot procedure; PostgreSQL remains the authoritative transactional source of truth.

Database upgrades follow: backup -> `alembic upgrade head` -> `alembic check` -> readiness/smoke -> application rollout. Destructive downgrade is not the default rollback strategy; restore from the validated pre-upgrade backup is.

## Data retention foundation

M15 documents data classes, owners and default recommended periods. Automatic deletion of business conversation/message/knowledge data is intentionally not enabled without tenant policy and legal requirements. Expired authentication/session records may be purged operationally; durable business data deletion remains explicit.

## Validation

Required before merge:

- Ruff check and formatter check;
- Pyright strict;
- unit regression;
- PostgreSQL/Redis integration regression;
- rate-limit tests including trusted-proxy behavior;
- DLQ/retry-cap tests;
- file magic/archive-bomb tests;
- SSRF regression tests;
- Alembic upgrade/check;
- frontend lint/typecheck/build;
- Docker Compose build/readiness/non-root checks;
- PostgreSQL backup/restore smoke;
- dependency/secret/container security workflow;
- load-tool syntax/smoke validation.

## Exit gate

Security, restore, upgrade and operational runbooks are documented and exercised for the supported Docker Compose deployment topology, and no public job path can retry indefinitely.