# Customers Manager HUB

Multi-tenant, omnichannel AI customer interaction platform for customer support, sales assistance, business knowledge, tool execution, customer memory, and human handoff.

> Current status: architecture/bootstrap phase. Production application code has not started yet.

## Product Direction

Customers Manager HUB is designed as a reusable commercial platform, not a channel-specific chatbot.

A tenant should eventually be able to:

- Connect channels such as Telegram and Website Chat.
- Enable/disable supported inbound message types per channel.
- Configure AI agents and prompt versions.
- Select AI provider/model independently for each AI task.
- Use OpenAI, Gemini, and future AI providers through adapters.
- Transcribe voice using a separately configured transcription model.
- Connect live business APIs and tools.
- Upload PDF/Excel knowledge and use RAG.
- Manage contacts, identities, conversations, and message history.
- Transfer conversations to human operators.
- Build structured customer memory and communication preferences.
- Review audit, trace, usage, cost, and analytics information.
- Run the platform using Docker.

## Architecture

Initial architecture:

- Modular monolith backend.
- Independent asynchronous workers.
- Multi-tenant by design.
- Provider-independent AI Gateway.
- Task-based AI model routing.
- Channel adapter architecture.
- PostgreSQL + pgvector.
- Redis queue/cache/coordination.
- S3-compatible object storage / MinIO for self-hosting.
- Next.js frontend.
- FastAPI backend.
- Docker Compose deployment.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the technical architecture.

## AI Task Routing

The platform does not assume one model handles all AI operations.

Conceptual examples:

| Task | Provider | Model |
|---|---|---|
| Customer response | Configurable | Configurable |
| Voice transcription | Configurable | Configurable |
| Intent classification | Configurable | Configurable |
| Conversation summary | Configurable | Configurable |
| Customer memory extraction | Configurable | Configurable |
| Embeddings | Configurable | Configurable |
| Complex reasoning/tool use | Configurable | Configurable |

Exact provider/model identifiers are stored as configuration rather than embedded throughout business logic.

## Initial MVP

Primary MVP scope includes:

- Multi-tenancy and RBAC.
- Telegram.
- Website Chat.
- Text and voice message processing.
- OpenAI adapter.
- Gemini adapter.
- AI task/model routing.
- Agent and prompt management.
- Generic REST/business tools.
- Google Sheets integration path.
- PDF/Excel knowledge ingestion.
- RAG.
- Contacts and conversations.
- Human handoff.
- Customer memory foundation.
- Policies.
- Audit and AI traces.
- Usage/cost and basic analytics.
- Docker Compose deployment.

See [PRD.md](PRD.md) for detailed product requirements and [ROADMAP.md](ROADMAP.md) for delivery sequencing.

## Repository Documents

- `AGENTS.md` — mandatory engineering and Codex rules.
- `PRD.md` — product requirements and MVP scope.
- `ARCHITECTURE.md` — architecture and module boundaries.
- `ROADMAP.md` — milestone sequence and delivery gates.
- `docs/adr/` — Architecture Decision Records.
- `.env.example` — documented non-secret environment configuration template.

## Development Governance

Before implementing a feature:

1. Read `AGENTS.md`.
2. Read relevant PRD/architecture sections.
3. Read applicable ADRs.
4. Work from a scoped task/specification.
5. Add appropriate tests.
6. Review tenant isolation, authorization, security, observability, and Docker impact.

Codex is treated as an implementation engineer and must not silently redesign the project architecture.

## Local Development

The repository is still in Bootstrap Milestone 0.

The final application containers and commands will be added during Milestone 1. The initial `docker-compose.yml` currently defines only the foundational data/infrastructure services required for upcoming development.

Typical Bootstrap preparation:

```bash
cp .env.example .env

docker compose up -d postgres redis minio
```

Do not commit `.env` or real credentials.

## Documentation Priority

Project decisions should be interpreted in this order:

1. Explicit approved product-owner decision.
2. Security and tenant-isolation invariants.
3. Accepted ADRs.
4. `PRD.md`.
5. `ARCHITECTURE.md`.
6. `AGENTS.md`.
7. Feature/task specifications.

## Current Branch Strategy

Bootstrap documentation and infrastructure are being prepared on:

```text
chore/project-bootstrap
```

The default branch should remain stable and feature work should use scoped branches.

## License

No public/open-source license has been selected yet. Until a license is explicitly added, do not assume reuse or redistribution rights.
