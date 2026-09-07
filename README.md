# Customers Manager HUB

Multi-tenant, omnichannel AI customer interaction platform for customer support, sales assistance, business knowledge, tool execution, customer memory, and human handoff.

> Current status: Milestones M0 through M10 are implemented and merged. The next roadmap milestone is M11 — Human Handoff & Operator Inbox.

## Product Direction

Customers Manager HUB is designed as a reusable commercial platform, not a channel-specific chatbot.

The implemented platform foundation already supports:

- Multi-tenant authentication, RBAC, tenant isolation, and audit foundations.
- Canonical contacts, external identities, conversations, messages, and attachment metadata.
- Telegram and Website Chat using the same channel/conversation core.
- Configurable Agents and versioned/published Prompts.
- Provider-independent AI task routing across OpenAI, Gemini, and deterministic test providers.
- Tool Runtime with tenant/agent authorization, encrypted credentials, Generic REST, business-reference, and Google Sheets paths.
- Tenant-scoped Knowledge Base and RAG using PDF/XLSX ingestion, S3-compatible object storage, embeddings, and pgvector retrieval.
- Telegram voice transcription through the configured `voice_transcription` task profile, with Gemini-first routing available by configuration and provider fallback.
- Docker Compose deployment for the API, Worker, Web, PostgreSQL/pgvector, Redis, and S3-compatible object storage.

The remaining MVP roadmap adds Human Handoff, Customer Memory, Policy Engine, Analytics/Cost, Production Hardening, and Commercial MVP validation.

## Current Roadmap Status

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
| M11 — Human Handoff & Operator Inbox | Next |
| M12 — Customer Memory & Intelligence | Planned |
| M13 — Policy Engine | Planned |
| M14 — Analytics, Usage & Cost | Planned |
| M15 — Security & Production Hardening | Planned |
| M16 — Commercial MVP Validation | Planned |

See [ROADMAP.md](ROADMAP.md) for milestone scope and exit gates.

## Architecture

Current architecture:

- Modular monolith backend with explicit internal boundaries.
- Independent asynchronous worker process using the same backend codebase/image.
- FastAPI backend API.
- Next.js Admin Console runtime.
- Separate lightweight Website Chat widget/runtime.
- Multi-tenant isolation by design.
- Provider-independent AI Gateway.
- Task-based AI model routing.
- Channel adapter architecture.
- PostgreSQL 18 + pgvector as the canonical datastore/vector store.
- Redis Streams for asynchronous channel/background processing and Redis for coordination.
- S3-compatible object storage with SeaweedFS as the current Docker reference backend.
- Docker Compose deployment.

The project deliberately avoids premature microservices. Module boundaries are designed so service extraction remains possible if scale or operational requirements justify it.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the technical architecture.

## AI Task Routing

The platform does not assume one model handles all AI operations.

| Task | Provider | Model |
|---|---|---|
| Customer response | Configurable | Configurable |
| Voice transcription | Configurable | Configurable |
| Intent classification | Configurable | Configurable |
| Conversation summary | Configurable | Configurable |
| Customer memory extraction | Configurable | Configurable |
| Embeddings | Configurable | Configurable |
| Complex reasoning/tool use | Configurable | Configurable |

Exact provider/model identifiers are configuration data rather than business-code dependencies.

For voice transcription, M10 recommends Gemini `gemini-3.5-transcribe` as the first configured route, while provider/model resolution still occurs through the tenant `voice_transcription` task profile and may fall back to other configured providers.

## Implemented End-to-End Capabilities

### Text conversation path

```text
Telegram / Website Chat
        ↓
Canonical Contact / Conversation / Message
        ↓
Agent + Published Prompt
        ↓
RAG + Approved Tools when needed
        ↓
AI Gateway / Task Routing
        ↓
Persisted Outbound Message
        ↓
Originating Channel
```

### Telegram voice path

```text
Telegram Voice
      ↓
Secure Media Retrieval
      ↓
voice_transcription Task Profile
      ↓
Configured Provider / Fallback
      ↓
Persist Transcript on Original Voice Message
      ↓
Normal Agent + RAG + Tool Runtime
      ↓
Telegram Response
```

Human operator takeover is intentionally the next capability and is not yet implemented.

## Repository Documents

- `AGENTS.md` — mandatory engineering and implementation rules.
- `PRD.md` — product requirements and MVP scope.
- `ARCHITECTURE.md` — architecture and module boundaries.
- `ROADMAP.md` — milestone sequence and delivery gates.
- `docs/adr/` — Architecture Decision Records.
- `docs/development/TECHNICAL_BASELINE.md` — approved runtime/toolchain baseline.
- `specs/` — milestone implementation specifications, including M1 through M10.
- `.env.example` — non-secret environment configuration template.

## Development Governance

Before implementing a feature:

1. Read `AGENTS.md`.
2. Read relevant PRD/architecture sections.
3. Read applicable ADRs.
4. Work from a scoped task/specification.
5. Add appropriate tests.
6. Review tenant isolation, authorization, security, observability, and Docker impact.

Material architecture changes require documentation/ADR updates before implementation proceeds.

## Local Development

Configure a local environment from the non-secret template:

```bash
cp .env.example .env
```

Start the current Docker Compose stack:

```bash
make up
make ps
```

The stack includes API, Worker, Web, PostgreSQL/pgvector, Redis, and S3-compatible object storage.

Useful commands:

```bash
make logs
make down
make clean
```

`make clean` removes local project volumes and therefore deletes local PostgreSQL, Redis, and object-storage development data.

Do not commit `.env` or real credentials.

## Validation Model

Each completed milestone is required by the roadmap to address tests, security, documentation, and Docker impact before completion.

M10's final validation covered frozen dependency installation, Ruff lint/format, Pyright strict, unit regression, PostgreSQL/pgvector migrations and Alembic drift checks, Redis-backed integration suites, voice transcription E2E behavior, Docker image builds, Compose startup/readiness, and non-root API/Worker execution.

A persistent repository CI workflow and enforced branch protection are being established separately; historical milestone validation has already been executed before merge.

## Documentation Priority

Project decisions should be interpreted in this order:

1. Explicit approved product-owner decision.
2. Security and tenant-isolation invariants.
3. Accepted ADRs.
4. `PRD.md`.
5. `ARCHITECTURE.md`.
6. `AGENTS.md`.
7. Feature/task specifications.

## Branch Strategy

The default branch is `main` and should remain stable. Feature, milestone, documentation, and infrastructure changes should use scoped branches and be merged through reviewed pull requests.

## License

No public/open-source license has been selected yet. Until a license is explicitly added, do not assume reuse or redistribution rights.
