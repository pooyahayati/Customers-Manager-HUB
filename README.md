# Customers Manager HUB

Multi-tenant, omnichannel AI customer interaction platform for customer support, sales assistance, business knowledge, live tools, customer memory, policy enforcement, human handoff, and operational analytics.

The repository implements the M0–M16 MVP roadmap as a reusable platform. Commercial acceptance is validated through persistent CI, Security gates, and the M16 fresh-install validation runbook.

## Product Direction

Customers Manager HUB is designed as a reusable business platform, not a channel-specific chatbot or a tenant-specific codebase.

Core capabilities include:

- Multi-tenant authentication, RBAC, tenant isolation, and privileged-action audit.
- Canonical contacts, external identities, conversations, messages, and attachment metadata.
- Telegram and Website Chat through the same channel/conversation runtime.
- Configurable Agents and versioned/published Prompts.
- Provider-independent AI Gateway with tenant-scoped task model routing across OpenAI/Gemini-compatible routes.
- Telegram voice transcription through the configured `voice_transcription` task profile.
- Safe Tool Runtime with encrypted credentials, authorization, approval, policy enforcement, and execution traces.
- Tenant-owned Knowledge Base/RAG for PDF/XLSX sources using embeddings and pgvector.
- Human handoff/operator queue with AI pause/resume and channel-correct human replies.
- Structured customer memory with fact/inference separation, provenance, confidence, freshness, and verification.
- Deterministic Policy Engine for autonomy, tools, approvals, message capabilities, business hours, and handoff triggers.
- AI usage/cost and operational analytics.
- Production hardening: rate limiting, request correlation, file/SSRF protections, finite retries/DLQ, backup/restore, load probes, dependency/secret/container scanning, and operational runbooks.
- Docker Compose deployment for API, Worker, Web, PostgreSQL/pgvector, Redis, and S3-compatible object storage.

## MVP Roadmap Status

| Milestone | Status |
|---|---|
| M0 — Project Governance & Bootstrap | Complete |
| M1 — Repository & Runtime Skeleton | Complete |
| M2 — Tenant, Authentication & RBAC | Complete |
| M3 — Contacts, Identities, Conversations & Messages | Complete |
| M4 — AI Gateway & Task Model Routing | Complete |
| M5 — Channel Framework & Telegram | Complete |
| M6 — Agent & Prompt Runtime | Complete |
| M7 — Website Chat Channel | Complete |
| M8 — Tool Runtime & Live Business Data | Complete |
| M9 — Knowledge Base & RAG | Complete |
| M10 — Voice Transcription End-to-End | Complete |
| M11 — Human Handoff & Operator Inbox | Complete |
| M12 — Customer Memory & Intelligence | Complete |
| M13 — Policy Engine | Complete |
| M14 — Analytics, Usage & Cost | Complete |
| M15 — Security & Production Hardening | Complete |
| M16 — Commercial MVP Validation | Complete through the repository validation gate |

See [ROADMAP.md](ROADMAP.md) for milestone scope and exit criteria and [Commercial MVP Validation](docs/operations/COMMERCIAL-MVP-VALIDATION.md) for the final scenario.

## Architecture

Current architecture:

- Modular monolith backend with explicit domain/runtime boundaries.
- Independent asynchronous Worker using the same backend codebase/image.
- FastAPI API.
- Next.js Admin Console.
- Lightweight embeddable Website Chat runtime.
- PostgreSQL 18 + pgvector as canonical relational/vector storage.
- Redis Streams for asynchronous channel/knowledge work and Redis for coordination/rate limiting.
- S3-compatible knowledge storage; SeaweedFS is the Docker reference implementation.
- Provider-independent AI Gateway and task-profile routing.
- Channel adapters for Telegram and Website Chat.
- Docker Compose deployment.

The project deliberately avoids premature microservices. Service extraction remains possible where scale or operational requirements justify it.

See [ARCHITECTURE.md](ARCHITECTURE.md).

## Core Conversation Paths

### Text

```text
Telegram / Website Chat
        ↓
Canonical Contact / Conversation / Message
        ↓
Policy + Handoff Gate
        ↓
Agent + Published Prompt + Approved Customer Memory
        ↓
RAG + Authorized Tools when required
        ↓
AI Gateway / Tenant Task Routing
        ↓
Persisted Outbound Message
        ↓
Originating Channel
```

### Telegram voice

```text
Telegram Voice
      ↓
Secure Media Retrieval
      ↓
voice_transcription Task Profile
      ↓
Configured Provider / Fallback
      ↓
Transcript on Original Voice Message
      ↓
Normal Policy + Agent + RAG + Tool Runtime
      ↓
Telegram Response
```

### Human handoff

```text
Conversation
     ↓
Policy / Customer / Tool Approval Escalation
     ↓
Queued → Claimed Human Handoff
     ↓
Autonomous AI Paused
     ↓
Operator Reply through Originating Channel
     ↓
Resolved / Cancelled → Optional AI Resume
```

## AI Configuration

Model selection is tenant-scoped and task-based. Supported task profiles include:

- customer response;
- voice transcription;
- intent classification;
- conversation summary;
- customer memory extraction;
- embedding.

OpenAI/Gemini API keys are deployment secrets supplied through runtime environment configuration. Secrets are not permitted inside AI route parameters. Provider/model identifiers remain configuration data rather than business-code dependencies.

## Local Development

Create local configuration from the non-secret template:

```bash
cp .env.example .env
```

Start the supported stack:

```bash
make up
make ps
```

Useful commands:

```bash
make infra-check
make logs
make migrate
make mvp-validate
make down
```

`make mvp-validate` creates a disposable `cmh_mvp_validation` PostgreSQL database and uses Redis DB 15 for the integration regression. It does not intentionally use or truncate the normal development database.

`make clean` removes local project volumes and therefore deletes local PostgreSQL, Redis, and object-storage data. Use it only for disposable environments.

Do not commit `.env` or real credentials.

## Commercial MVP Validation

M16 acceptance combines three persistent gates:

1. **Docker and Compose** — build, migration, backup/restore smoke, startup/readiness, load probe, and non-root runtime.
2. **Backend integration** — the commercial validation harness runs the complete `test_*_integration.py` regression and maps it to all required business capabilities.
3. **Security** — locked Python/Node dependency audit, repository secret scan, and runtime-rootfs container vulnerability scan.

External OpenAI/Gemini/Telegram/business-API calls remain environment-specific smoke tests. CI uses deterministic adapters so no repository secret or third-party availability is required.

See [docs/operations/COMMERCIAL-MVP-VALIDATION.md](docs/operations/COMMERCIAL-MVP-VALIDATION.md).

## Production Operations

M15 production hardening includes:

- Redis-backed distributed rate limiting;
- trusted-proxy handling;
- request correlation and structured allowlisted logs;
- production configuration guards;
- PDF/XLSX ingestion hardening;
- Tool Runtime SSRF controls;
- finite Redis Stream retry budgets and DLQs;
- PostgreSQL backup/guarded restore procedure;
- migration/upgrade, rollback, retention, and incident runbooks;
- load-probe smoke;
- persistent dependency, secret, and container scanning.

See `docs/operations/` for operational procedures.

## Repository Documents

- `AGENTS.md` — engineering/implementation rules.
- `PRD.md` — product requirements and MVP scope.
- `ARCHITECTURE.md` — architecture and invariants.
- `ROADMAP.md` — milestone sequence and exit gates.
- `specs/` — milestone implementation/acceptance specifications.
- `docs/adr/` — Architecture Decision Records.
- `docs/operations/` — production and commercial validation runbooks.
- `.env.example` — non-secret environment template.

## Validation and Governance

Persistent workflows validate:

- Ruff lint/format;
- Pyright strict typing;
- unit regression;
- PostgreSQL/pgvector migration and Alembic drift;
- Redis/PostgreSQL integration regression;
- frontend lint/typecheck/build;
- Docker/Compose runtime and readiness;
- backup/restore and load probes;
- Python/Node dependency vulnerabilities;
- repository secrets;
- runtime container HIGH/CRITICAL fixed vulnerabilities.

The default branch is `main`; feature/milestone work should remain scoped and be merged after validation.

## License

No public/open-source license has been selected. Until a license is explicitly added, do not assume reuse or redistribution rights.
