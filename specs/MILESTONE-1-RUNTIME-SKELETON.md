# Milestone 1 — Runtime Skeleton Specification

## Customers Manager HUB

**Status:** Approved implementation specification candidate  
**Purpose:** Define the exact non-feature application skeleton Codex must implement before product-domain development begins.

---

## 1. Goal

Create a minimal, production-oriented runtime skeleton that proves the project can run as:

- FastAPI backend API.
- Independent backend worker process from the same Python codebase/image.
- Next.js frontend.
- PostgreSQL + pgvector infrastructure.
- Redis infrastructure.
- Docker Compose application stack.
- Reproducible Python and Node dependency environments.

This milestone must establish runtime, configuration, health checks, tests, quality checks, and Docker build behavior only.

It must not implement business features.

---

## 2. Mandatory Constraints

Codex must read before implementation:

1. `AGENTS.md`
2. `PRD.md`
3. `ARCHITECTURE.md`
4. `ROADMAP.md`
5. `docs/development/TECHNICAL_BASELINE.md`
6. Relevant ADRs under `docs/adr/`
7. This specification

Architecture constraints:

- Modular monolith.
- API and worker share the backend codebase.
- No business-domain feature implementation.
- No OpenAI/Gemini integration yet.
- No Telegram integration yet.
- No authentication/RBAC yet.
- No ORM domain models yet.
- No speculative framework abstractions.
- No microservices.
- No n8n dependency.
- No object-storage implementation yet.

---

## 3. Required Repository Shape

Create only the structure needed for runnable code.

```text
Customers-Manager-HUB/
├── apps/
│   ├── api/
│   │   ├── src/
│   │   │   └── customers_manager_hub/
│   │   ├── tests/
│   │   └── Dockerfile
│   └── web/
│       ├── app/
│       ├── public/
│       └── Dockerfile
├── infra/
│   └── docker/
│       └── postgres/
├── specs/
├── pyproject.toml
├── uv.lock
├── package.json
├── pnpm-lock.yaml
├── pnpm-workspace.yaml
├── compose.yaml
├── Makefile
├── .python-version
└── .node-version
```

Do not create empty placeholder package trees for future modules.

Create directories only when they contain functional files.

---

## 4. Backend Runtime

### 4.1 Python

Use the runtime defined by `.python-version` and the technical baseline.

Use `uv` as the authoritative Python dependency manager.

The root `pyproject.toml` must be the authoritative Python project configuration.

Do not add:

- Poetry.
- Pipenv.
- Conda.
- Hand-maintained `requirements.txt` as another dependency authority.

### 4.2 Backend framework

Use:

- FastAPI.
- Pydantic v2-compatible dependencies.
- Uvicorn for the HTTP runtime.

SQLAlchemy/Alembic may be included only if they are required to establish and test database connectivity/migration infrastructure in this milestone. Do not create product tables.

### 4.3 Backend package

Canonical import package:

```text
customers_manager_hub
```

The backend package should contain only the minimum runtime concerns needed now, such as:

```text
customers_manager_hub/
├── __init__.py
├── main.py
├── config.py
├── logging.py
├── health.py
└── worker.py
```

Exact splitting may vary slightly if a simpler structure is clearer.

Do not create `domain/`, `repositories/`, `services/`, `providers/`, or similar empty/future directories yet.

---

## 5. Backend Configuration

Create typed application settings using Pydantic settings or an equivalent Pydantic v2-compatible approach.

Configuration must read from environment variables.

At minimum support:

- `APP_ENV`
- `APP_NAME`
- `APP_DEBUG`
- `APP_LOG_LEVEL`
- `POSTGRES_*` or `DATABASE_URL`
- `REDIS_URL`

Rules:

- Development defaults may exist where safe.
- Production-sensitive configuration must not silently fall back to unsafe secrets.
- No credentials are hard-coded into Python source.
- Do not require OpenAI/Gemini/Telegram credentials to boot the runtime skeleton.

---

## 6. Backend Health Endpoints

Implement exactly two operational endpoints:

### `GET /health`

Purpose: liveness.

Requirements:

- Does not depend on external services.
- Returns HTTP 200 when the API process is running.
- Minimal JSON response.

Example semantics:

```json
{"status": "ok"}
```

### `GET /ready`

Purpose: readiness.

Requirements:

- Check PostgreSQL connectivity.
- Check Redis connectivity.
- Return HTTP 200 only when required runtime dependencies are reachable.
- Return a non-200 readiness status if a required dependency is unavailable.
- Do not leak credentials or connection strings in error responses.

Do not create product API endpoints in this milestone.

---

## 7. Database Bootstrap

### 7.1 Connectivity

The application must connect to PostgreSQL through an explicit infrastructure helper.

Use SQLAlchemy 2.x if introduced.

Do not create tenant, user, contact, conversation, or other product models yet.

### 7.2 pgvector

The runtime must verify that the PostgreSQL image contains the `vector` extension capability.

If Alembic migration infrastructure is included now, the initial migration may only enable required infrastructure extensions such as:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

No product tables.

### 7.3 Migrations

If Alembic is added, it must be runnable and contain only infrastructure-level initialization.

Do not auto-run schema migrations as an implicit side effect of importing application code.

---

## 8. Redis Bootstrap

Provide one simple Redis client abstraction/helper sufficient for:

- Readiness check.
- Future worker/queue integration.

Do not implement queue semantics yet unless the selected worker skeleton genuinely requires them.

Redis must not become the source of truth for persistent application state.

---

## 9. Worker Skeleton

Create an independently runnable worker entry point using the same Python package/image as the API.

For this milestone the worker only needs to prove:

- Configuration loads correctly.
- Logging initializes correctly.
- Redis/PostgreSQL dependencies can be reached if the selected worker lifecycle requires them.
- The process can start cleanly and remain alive in an appropriate worker loop/runtime.

Do not implement fake jobs merely to demonstrate queues.

Do not introduce Celery, Dramatiq, RQ, Arq, Taskiq, or another queue framework unless this milestone separately decides that framework through an ADR.

Preferred approach for this milestone:

- Keep the worker entry point minimal.
- Defer actual queue-library selection to the event-processing implementation milestone/ADR.

The Compose worker service may therefore be omitted until the worker has meaningful runtime behavior, or may run a minimal explicit process only if it adds real validation value.

Choose the simpler maintainable option.

---

## 10. Backend Logging

Implement structured application logging foundation.

Requirements:

- Respect `APP_LOG_LEVEL`.
- Include timestamp, level, logger/message.
- Prefer JSON-friendly structured logging for container environments.
- Do not add a large logging framework unless needed.
- Never log environment secrets.

No OpenTelemetry export is required yet.

---

## 11. Backend Tests

Create only tests that verify the runtime skeleton.

Required tests:

1. `/health` returns success.
2. Configuration parsing works for required development values.
3. `/ready` behavior can be tested without requiring paid/external APIs.
4. No test calls OpenAI, Gemini, Telegram, or other paid/external services.

Prefer mocking dependency readiness checks in unit/API tests.

A separate infrastructure smoke test may use Compose PostgreSQL/Redis if useful, but it must not be required for every fast unit test run.

---

## 12. Backend Quality Configuration

Configure in `pyproject.toml`:

- Ruff formatting.
- Ruff linting.
- Pyright typing.
- Pytest.

Rules:

- Do not add Black.
- Do not add Flake8.
- Do not add mypy in parallel with Pyright.
- Avoid duplicate tools for the same job.

Required commands after implementation:

```text
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

---

## 13. Frontend Runtime

### 13.1 Workspace

Use root pnpm workspace management.

Root files:

```text
package.json
pnpm-workspace.yaml
pnpm-lock.yaml
```

The workspace initially contains only:

```text
apps/web
```

Do not create shared TypeScript packages yet.

### 13.2 Next.js

Create a minimal Next.js application using:

- App Router.
- TypeScript strict mode.
- Current approved Next.js 16.3.x line from the technical baseline.

Do not add UI frameworks in this milestone unless Next.js generation strictly requires them.

Specifically do not add without a later UI decision:

- Material UI.
- Chakra.
- Ant Design.
- shadcn component collection.
- Tailwind if it is not intentionally selected.
- State-management libraries.

The initial page should be intentionally minimal.

It may show:

```text
Customers Manager HUB
System bootstrap running
```

No dashboard mockup.

No authentication UI.

No fake product screens.

---

## 14. Frontend Health

The frontend container only needs a Docker-level HTTP health check if practical.

Do not build a separate product health subsystem into Next.js.

---

## 15. Frontend Quality

Use only the tools required by the chosen Next.js setup.

Requirements:

- TypeScript strict mode.
- One lint path.
- One formatting path.

Do not add overlapping lint/format stacks.

If ESLint is retained by the chosen Next.js version/setup, configure it minimally.

Do not add Prettier unless it provides a clear required formatting role not already covered by the selected frontend toolchain.

---

## 16. Dockerfiles

### Backend Dockerfile

Create:

```text
apps/api/Dockerfile
```

Requirements:

- Explicit Python 3.14 base image patch/tag consistent with baseline.
- Use `uv` for reproducible install from committed lockfile.
- Multi-stage build if it materially reduces runtime image complexity/size.
- Non-root runtime user where practical.
- No development-only tooling in final image where avoidable.
- No secrets baked into image.

The same backend image should be suitable for API and future worker command variants.

### Frontend Dockerfile

Create:

```text
apps/web/Dockerfile
```

Requirements:

- Explicit Node 24 LTS base image line.
- pnpm via Corepack or another standard pinned method.
- Frozen lockfile install.
- Multi-stage production build.
- Non-root runtime where practical.
- No secrets baked into browser/server bundle unintentionally.

---

## 17. Compose Changes

Extend `compose.yaml` only after the application containers are runnable.

Add:

### `api`

- Build from backend Dockerfile.
- Depend on PostgreSQL and Redis readiness.
- Expose configurable port, default `8000`.
- Health check using `/health` or `/ready` as appropriate.
- Load runtime environment.

### `web`

- Build from frontend Dockerfile.
- Expose configurable port, default `3000`.
- Depend on API only if the runtime actually requires it.

### Worker

Add only if the worker process provides meaningful runnable behavior at the end of this milestone.

Do not add placeholder services that immediately exit or perform fake work.

### Object storage

Do not add object storage in this milestone.

---

## 18. Environment Template Changes

Update `.env.example` only for variables the implemented skeleton actually uses.

Remove or clearly retain future variables only if they serve documentation value without causing confusion.

The preferred outcome is a concise `.env.example` focused on current runtime needs.

Do not add real secrets.

---

## 19. Makefile Changes

After application skeleton exists, extend the existing `Makefile` minimally.

Allowed useful targets:

```text
setup
up
down
restart
ps
logs
infra-check
format
lint
typecheck
test
check
```

Target behavior:

- `format`: apply formatting.
- `lint`: backend + frontend lint where configured.
- `typecheck`: backend + frontend type checks.
- `test`: current automated tests.
- `check`: non-mutating quality gate combining validation commands.

Do not add dozens of wrappers for trivial one-off commands.

---

## 20. Dependency Rules

Before adding each dependency, Codex must be able to state why the skeleton requires it.

Avoid dependencies that belong to later milestones, including:

- OpenAI SDK.
- Gemini SDK.
- Telegram libraries.
- LangChain.
- LangGraph.
- Vector/RAG libraries beyond PostgreSQL infrastructure requirements.
- Authentication libraries.
- JWT libraries.
- ORM models for business entities.
- HTTP clients for tool integrations.
- Object-storage SDKs.

This milestone should have a small dependency graph.

---

## 21. Explicitly Out of Scope

Do not implement:

- Tenants.
- Users.
- Authentication.
- RBAC.
- Contacts.
- Conversations.
- Messages.
- Channels.
- Telegram.
- Website chat widget.
- AI provider gateway.
- Prompt management.
- Agent runtime.
- Tool runtime.
- RAG.
- Voice transcription.
- Human handoff.
- Customer memory.
- Policy engine.
- Analytics.
- Billing.

No fake placeholders for these features should be created.

---

## 22. Expected Deliverables

At completion, Codex should have produced only the files necessary for:

1. Runnable FastAPI API.
2. Runnable minimal Next.js frontend.
3. Reproducible Python dependency lock.
4. Reproducible pnpm dependency lock.
5. API and frontend Dockerfiles.
6. Compose application integration.
7. Minimal runtime tests.
8. Ruff/Pyright/Pytest configuration.
9. TypeScript strict/lint configuration.
10. Updated Makefile commands.
11. Updated concise `.env.example` if required.
12. Minimal README setup instructions update if commands changed.

---

## 23. Acceptance Criteria

Milestone 1 implementation is acceptable only if all applicable checks pass.

### Local/Dependency

```text
uv sync --frozen
pnpm install --frozen-lockfile
```

### Backend quality

```text
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

### Frontend quality

The exact commands depend on generated package scripts, but must include:

```text
pnpm lint
pnpm typecheck
pnpm build
```

or equivalent root workspace commands.

### Docker

```text
docker compose -f compose.yaml config --quiet
docker compose -f compose.yaml build
docker compose -f compose.yaml up -d
```

### Runtime

- PostgreSQL becomes healthy.
- Redis becomes healthy.
- API becomes healthy.
- `/health` returns HTTP 200.
- `/ready` returns HTTP 200 when PostgreSQL/Redis are ready.
- Web application responds on configured port.
- No container is in a restart loop.
- No required service uses `latest` image tags.

### Repository hygiene

- No real secrets committed.
- No generated `.venv`/`node_modules` committed.
- Lockfiles committed.
- No unnecessary future feature directories.

---

## 24. Codex Output Requirements

When Codex completes the task, it must report:

1. Files created/changed.
2. Dependencies added and why each is required.
3. Commands run.
4. Test/lint/type/build results.
5. Any assumptions.
6. Any deviations from this specification.
7. Any unresolved risks.

Codex must not silently change architecture decisions.

---

## 25. Review Gate

After Codex implementation:

1. ChatGPT/Senior Technical Lead reviews the complete diff.
2. Security and dependency scope are checked.
3. Docker/Compose behavior is checked.
4. Tests and type/lint results are reviewed.
5. Any fixes are sent back as narrowly scoped tasks.
6. Only after approval may Milestone 1 be considered complete.

No Milestone 2 product-domain work starts before this review gate passes.
