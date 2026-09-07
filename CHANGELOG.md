# Changelog

All notable changes to Customers Manager HUB are documented here.

## [0.1.0] - 2026-09-08

First commercial MVP release candidate.

### Added

- Multi-tenant authentication, RBAC, tenant isolation, and audit events.
- Canonical contacts, external identities, conversations, messages, and attachments.
- Provider-neutral AI Gateway with OpenAI/Gemini routing, retry/fallback, task profiles, and execution traces.
- Telegram channel framework with inbound/outbound text and voice metadata.
- Versioned Agent/Prompt authoring, publishing, channel assignment, and durable Agent runs.
- Website Chat channel and embeddable widget foundation.
- Governed Tool Runtime with credentials, permissions, approval flows, execution traces, REST/business-reference/Google Sheets read adapters, and SSRF protections.
- Knowledge/RAG for PDF/XLSX ingestion, object storage, embeddings, pgvector retrieval, provenance, and tenant scoping.
- Voice transcription with Gemini-first/OpenAI fallback routing into the normal Agent/RAG/Tool path.
- Human handoff/operator inbox state machine with claim/release/resolve/cancel, human replies, AI pause/resume, and assist suggestions.
- Structured customer memory with fact/inference separation, confidence/freshness/provenance, extraction, verification, and bounded Agent context injection.
- Deterministic tenant Policy Engine for tool access, approvals, message capability, business hours, autonomy, and handoff rules.
- Analytics for AI usage/cost, conversations, response time, handoff, automation, and tool outcomes.
- Production hardening: Redis rate limiting, request correlation, structured logging, finite retries/DLQ, file parser hardening, production configuration guards, backup/restore scripts, load probe, dependency/secret/container scanning, and non-root runtime checks.
- Commercial MVP validation harness covering the 17-step roadmap acceptance scenario.
- Versioned release pipeline for GHCR application/PostgreSQL images and GitHub Releases.
- Deployment smoke script and infrastructure-neutral release/deployment runbook.

### Security

- Provider, Telegram, tool, and object-storage credentials remain server-side and are excluded from AI route parameters and normal API responses.
- Runtime container scans block fixed HIGH/CRITICAL vulnerabilities.
- PostgreSQL, Redis, and reference object-storage host ports are loopback-bound by default.
- Production configuration rejects insecure defaults and missing required encryption material.

### Operations

- Docker Compose build/start/migrate/readiness validation.
- PostgreSQL backup/restore smoke validation.
- Release images are versioned and tied to the exact `main` source SHA.
- Release publication requires successful CI and Security runs on the exact release commit.

### Known deployment boundary

A specific production host/cloud account, DNS zone, TLS configuration, and real provider/channel credentials are intentionally not stored in the repository. Those environment-specific values are required to perform the final external deployment and real-provider smoke test.
