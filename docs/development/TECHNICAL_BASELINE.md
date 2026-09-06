# Technical Baseline

## Customers Manager HUB

**Status:** Approved baseline candidate for pre-coding setup  
**Purpose:** Freeze the initial development/runtime toolchain before Codex or human contributors generate application code.

---

## 1. Baseline Principle

The project should begin from stable, production-supported runtimes rather than release candidates or speculative versions.

Patch versions may be updated through dependency maintenance, but major runtime changes require deliberate review.

The authoritative dependency versions for application libraries will ultimately be the committed lockfiles, not documentation prose.

---

## 2. Python Runtime

### Decision

Use:

```text
Python 3.14.x
Initial bootstrap target: 3.14.7
```

Do not use Python 3.15 release candidates for the production baseline.

### Rationale

- Python 3.14 is the current stable feature series at bootstrap time.
- Python 3.15 is still in release-candidate stage and should not become the initial production dependency baseline.
- The project needs broad compatibility with FastAPI, SQLAlchemy, AI SDKs, parsing libraries, PostgreSQL drivers, and observability libraries.

### Version files

During runtime skeleton setup create:

```text
.python-version
```

with the selected Python version.

The Docker image must use the same Python major/minor version.

---

## 3. Python Package & Environment Manager

### Decision

Use **uv** as the Python project/package/environment manager.

Initial tool version reference at bootstrap time:

```text
uv 0.12.x
```

### Responsibilities

uv will be used for:

- Python environment creation.
- Dependency resolution.
- Lockfile generation.
- Dependency synchronization.
- Running Python developer commands.

### Required repository files after backend skeleton creation

```text
pyproject.toml
uv.lock
.python-version
```

Do not maintain parallel Poetry/Pipenv/requirements.txt dependency authorities unless a specific deployment/export use case requires a generated requirements file.

`uv.lock` is committed.

`.venv/` is never committed.

---

## 4. Backend Framework

### Decision

Use:

```text
FastAPI 0.141.x baseline
Pydantic v2 generation
SQLAlchemy 2.x generation
Alembic for schema migrations
```

Exact compatible patch versions are resolved and locked when the API skeleton is created.

### Rules

- Route handlers remain thin.
- Business logic belongs in application/domain modules.
- SQLAlchemy models/repositories must not become the domain architecture itself.
- Database schema changes always use Alembic migrations.
- Provider SDKs do not appear in domain/business modules.

---

## 5. Python Code Quality Toolchain

### Decision

Use the following baseline:

```text
Ruff       -> linting + formatting
Pyright    -> static type checking
Pytest     -> tests
pytest-asyncio or equivalent only where async tests require it
```

### Rules

- Ruff owns formatting; do not add Black in parallel without a reason.
- Ruff configuration belongs in `pyproject.toml` unless separation becomes useful.
- Static typing should begin strict enough to prevent untyped architecture boundaries.
- Public application interfaces require type hints.
- Default tests must not call paid AI APIs.

### Expected commands

Final commands may be wrapped through Make/Task scripts, but the conceptual checks are:

```text
ruff format --check
ruff check
pyright
pytest
```

---

## 6. Node.js Runtime

### Decision

Use an LTS release for production development.

```text
Node.js 24 LTS
```

Do not adopt a Current/non-LTS Node release as the initial production baseline.

### Version file

During frontend skeleton creation create one canonical runtime marker, preferably:

```text
.node-version
```

and also define the Node engine in `package.json`.

Avoid maintaining conflicting Node version declarations.

---

## 7. JavaScript Package Manager

### Decision

Use **pnpm**.

Initial bootstrap generation:

```text
pnpm 12.x
Initial reference: 12.3.4
```

### Rationale

- Strong workspace support.
- Efficient dependency storage.
- Strict dependency behavior.
- Appropriate for a repository containing frontend and possible future shared TypeScript packages.

### Rules

- Commit `pnpm-lock.yaml`.
- Pin the package manager through the `packageManager` field in the root `package.json` when it is created.
- Do not commit `node_modules/`.
- Do not use npm/yarn lockfiles in parallel.

---

## 8. Frontend Framework

### Decision

Use:

```text
Next.js 16.3.x Active LTS baseline
TypeScript strict mode
React version required by the selected Next.js release
```

Initial reference patch:

```text
Next.js 16.3.3
```

### Rules

- TypeScript `strict` must be enabled.
- Browser code never owns tenant authorization or privileged secrets.
- API contracts must use a deliberate typed strategy rather than manually duplicated arbitrary shapes.
- Server/client component boundaries should remain explicit.

---

## 9. Frontend Code Quality Toolchain

Initial baseline:

```text
TypeScript compiler -> type checking
ESLint              -> framework/ecosystem lint rules where still required
Prettier             -> only if Next.js/ESLint formatting does not provide a single agreed formatter
```

Before implementation, avoid installing overlapping formatters/lint systems.

The first frontend task must define one authoritative formatting path.

---

## 10. Docker Runtime

### Host baseline

Development/production hosts should use a maintained modern Docker Engine release.

At bootstrap time the current Engine line is:

```text
Docker Engine 29.x
```

Do not pin server documentation to one patch forever; require a supported Engine release compatible with the project Compose specification.

### Compose

Use the modern `docker compose` plugin, not legacy Python `docker-compose` v1.

Current bootstrap reference:

```text
Docker Compose 5.x
```

### Repository filename

Preferred modern canonical file name for future cleanup:

```text
compose.yaml
```

The repository currently contains `docker-compose.yml`. Before application containers are added, decide and normalize to one filename only.

### Container rules

Application containers must:

- Use explicit base image versions.
- Avoid `latest` tags.
- Run as non-root where practical.
- Have health checks when meaningful.
- Use multi-stage builds where they reduce runtime image size/attack surface.
- Receive secrets at runtime, never bake them into layers.
- Use reproducible dependency lockfiles.

---

## 11. Initial Infrastructure Services

The local/self-hosted development baseline remains:

```text
PostgreSQL + pgvector
Redis
S3-compatible object storage / MinIO
```

Exact image versions must be verified and pinned before application development begins.

Rules:

- No `latest` image tags.
- Persistent volumes are named explicitly.
- Health checks are required.
- Application startup must depend on readiness semantics, not arbitrary sleep delays.
- Database is the source of truth; Redis is disposable infrastructure.

---

## 12. Repository Layout Baseline

Target layout:

```text
Customers-Manager-HUB/
├── apps/
│   ├── api/
│   └── web/
├── docs/
│   ├── adr/
│   ├── development/
│   ├── deployment/
│   ├── security/
│   └── api/
├── infra/
│   └── docker/
├── specs/
│   ├── modules/
│   └── tasks/
├── tests/
├── AGENTS.md
├── PRD.md
├── ARCHITECTURE.md
├── ROADMAP.md
└── ...
```

Do not create empty directory trees purely for appearance. Create directories when they receive their first meaningful file.

---

## 13. Development Command Interface

The project should expose a small, memorable command layer so contributors and Codex do not need to memorize framework-specific commands.

Recommended solution:

```text
Makefile
```

Expected commands after skeleton implementation:

```text
make setup
make up
make down
make logs
make test
make lint
make typecheck
make format
make check
```

On systems where GNU Make is unavailable, the underlying commands remain documented and usable directly.

Do not hide critical production behavior behind opaque shell scripts.

---

## 14. Git Hooks / Pre-commit

Use automated local validation, but do not make developer onboarding fragile.

Recommended baseline:

```text
pre-commit framework for lightweight checks
```

Candidate checks:

- Trailing whitespace / EOF normalization.
- Secret pattern detection where practical.
- Ruff lint/format.
- Relevant config validation.

Full integration tests should run in CI rather than every Git commit.

---

## 15. CI Baseline

Use GitHub Actions after the runtime skeleton exists.

Minimum pull-request CI should eventually include:

```text
Backend lint
Backend formatting check
Backend type check
Backend unit/integration tests
Frontend lint/type check
Frontend tests when present
Docker build validation
Compose configuration validation
Secret scanning
Dependency/security scanning
```

CI configuration is part of Milestone 1, but should be added after real application manifests exist so workflows test actual code rather than placeholders.

---

## 16. Dependency Update Policy

### Runtime major/minor upgrades

Require explicit review.

Examples:

- Python 3.14 -> 3.15
- Node 24 -> later LTS
- Next.js major upgrade
- PostgreSQL major upgrade

### Patch/minor libraries

May be handled through normal maintenance PRs provided tests pass and compatibility is reviewed.

### Security updates

High/critical security patches take priority and may justify expedited maintenance.

Do not use unrestricted wildcard dependency ranges for production dependencies.

---

## 17. Lockfile Policy

Lockfiles are mandatory and committed.

Expected:

```text
uv.lock
pnpm-lock.yaml
```

Docker base images should eventually be pinned by explicit version tag; production release pipelines may additionally pin image digests.

---

## 18. Environment Strategy

Use `.env.example` as documentation only.

Local real secrets go into an ignored `.env` or environment-specific secret mechanism.

Production secrets should be injected through the deployment environment or secret manager.

Never commit:

- API keys.
- Telegram tokens.
- Database production credentials.
- JWT/signing secrets.
- MinIO production secrets.
- Third-party integration secrets.

Configuration validation must fail clearly if production-critical secrets are absent.

---

## 19. AI Model Configuration Rule

AI provider/model choices are runtime configuration, not source-code constants.

Environment variables may provide bootstrap defaults only.

Final authority hierarchy:

```text
Tenant Task Profile configuration
        ↓
Platform/default Task Profile
        ↓
Bootstrap environment default
```

Examples:

```text
customer_response -> configurable provider/model
voice_transcription -> configurable provider/model
intent_classification -> configurable provider/model
embedding -> configurable provider/model
```

Provider model identifiers must be validated against current provider capabilities when implemented.

---

## 20. Pre-Coding Work Remaining

Before Codex receives the first application-code task, complete these actions in order:

1. Pin infrastructure service images after checking current supported PostgreSQL/pgvector, Redis, and MinIO releases.
2. Normalize Docker Compose naming/configuration and validate the Compose file.
3. Add runtime marker files (`.python-version`, `.node-version`) after final toolchain approval.
4. Create the Milestone 1 runtime-skeleton technical specification.
5. Create the initial backend/frontend package-management decisions as ADR(s) if needed.
6. Define the exact repository skeleton and module boundary conventions.
7. Define local developer commands (`Makefile`) specification.
8. Define CI acceptance checks.
9. Open a Bootstrap PR for review before application code begins.

No feature implementation should start before these pre-coding actions are complete.

---

## 21. Change Control

This baseline is intentionally conservative.

If a newer runtime or tool is proposed during implementation, compatibility and operational benefit must be evaluated rather than upgrading merely because a newer release exists.
