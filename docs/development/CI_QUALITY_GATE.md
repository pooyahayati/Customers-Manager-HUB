# CI & Quality Gate

## Customers Manager HUB

**Status:** Approved pre-coding baseline  
**Purpose:** Define the minimum CI checks required once the runtime skeleton exists.

---

## 1. Principle

CI must be small, fast, deterministic, and directly tied to real project code.

Do not create placeholder workflows for components that do not exist yet.

The first GitHub Actions workflow should be added only after `apps/api` and `apps/web` exist with real manifests and lockfiles.

---

## 2. Pull Request Quality Gate

A pull request that changes application code must not be considered merge-ready unless the relevant checks pass.

Minimum required checks:

### Backend

```text
ruff format --check
ruff check
pyright
pytest
```

### Frontend

```text
pnpm lint
pnpm typecheck
pnpm test
```

If the frontend skeleton does not yet contain meaningful tests, `pnpm test` may be omitted until the first testable frontend behavior is introduced. It must not be replaced by a fake passing script.

### Docker / Infrastructure

```text
docker compose -f compose.yaml config --quiet
```

After API/Web Dockerfiles exist, CI must also verify their image builds.

---

## 3. CI Workflow Shape

Prefer one understandable pull-request workflow initially rather than many fragmented workflow files.

Recommended logical jobs:

```text
backend
frontend
compose
```

Add separate security/build jobs only when they perform real work that cannot be cleanly included in these jobs.

Do not add complex matrices unless multiple supported runtime versions become a real product requirement.

The project initially supports one approved Python version and one approved Node LTS line.

---

## 4. Dependency Installation Rules

CI must install dependencies from committed lockfiles.

Python:

```text
uv.lock
```

JavaScript:

```text
pnpm-lock.yaml
```

CI must fail when lockfiles and manifests are inconsistent.

Do not perform unrestricted dependency upgrades inside CI.

---

## 5. External Services in Tests

Default CI tests must not call paid AI providers or live customer integrations.

Do not call:

- OpenAI production APIs.
- Gemini production APIs.
- Telegram production bots.
- Customer APIs.
- External CRMs.

Use mocks/fakes or local test infrastructure at provider boundaries.

PostgreSQL and Redis may be started as CI service containers when integration tests require them.

---

## 6. Secrets

CI must not require real production secrets for normal tests.

GitHub Actions secrets should be introduced only when a real CI/CD operation requires them.

Never place credentials directly inside workflow YAML.

Do not expose secrets in test output or logs.

---

## 7. Security Checks

Keep the initial security gate practical.

Required once application dependencies exist:

- Dependency vulnerability review/scanning through a maintained tool or GitHub-native capability.
- Secret scanning where available.
- Container build must not embed `.env` or repository secrets.

Do not add multiple overlapping scanners that generate duplicate noise without improving coverage.

---

## 8. Branch / Merge Gate

Once the first real CI workflow is active, `main` should require successful CI before merge where repository settings permit it.

Recommended merge requirements:

- Pull request required.
- CI checks pass.
- No unresolved critical review comments.
- No merge when required migration/documentation changes are missing.

Do not require manual approval for every trivial maintenance change unless team size/workflow later requires it.

---

## 9. Docker Build Gate

Once application Dockerfiles exist, CI must verify at least:

```text
API image builds
Web image builds
Worker image/entrypoint builds if separate
Compose configuration resolves successfully
```

A successful local framework test does not compensate for a broken Docker build because Docker is a primary deployment contract of this product.

---

## 10. Migration Gate

Once Alembic is introduced, database schema changes must include migrations.

CI should eventually verify that:

- Migrations apply successfully to a clean test database.
- Application tests run against the migrated schema.

Do not auto-generate and commit migrations inside CI.

---

## 11. CI Failure Policy

Do not bypass a failing required check by weakening the check unless the check itself is demonstrably incorrect.

Fix the code, test, dependency, or workflow cause.

Temporary skips must be explicit, justified, and removed as soon as the blocker is resolved.

---

## 12. Definition of CI Ready for Milestone 1

Milestone 1 CI is complete when a pull request can automatically verify:

1. Backend formatting/linting.
2. Backend static typing.
3. Backend tests.
4. Frontend linting/type checking.
5. Frontend tests when meaningful tests exist.
6. Compose configuration validity.
7. API/Web Docker image builds.
8. No live paid AI/integration calls are required.

---

## 13. What Not to Add Yet

Do not add before a demonstrated need:

- Multi-version Python/Node test matrices.
- Kubernetes validation.
- Release publishing pipelines.
- Docker registry publishing.
- Preview environments.
- Deployment to production.
- End-to-end browser farms.
- Multiple overlapping security scanners.
- Scheduled nightly workflows.

These can be introduced later when supported product/runtime requirements justify them.

---

## 14. Implementation Instruction for Codex

When the Milestone 1 runtime skeleton is implemented, Codex must add the smallest GitHub Actions workflow that satisfies this document using the actual committed project commands and lockfiles.

Codex must not invent passing placeholder scripts merely to satisfy CI.
