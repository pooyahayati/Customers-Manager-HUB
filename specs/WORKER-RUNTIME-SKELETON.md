# Worker Runtime Skeleton — Execution Scope

**Branch:** `feat/worker-runtime-skeleton`  
**Parent specification:** `specs/MILESTONE-1-RUNTIME-SKELETON.md`

## Goal

Complete the Milestone 1 backend worker runtime with the smallest independently runnable process that shares the existing backend package and Docker image.

## Scope

- Add `customers_manager_hub.worker` as the worker module entry point.
- Reuse existing typed settings and structured logging.
- Validate PostgreSQL and Redis readiness at worker startup.
- Fail fast when required runtime dependencies are unavailable.
- Remain alive without fake jobs or queue semantics.
- Handle `SIGTERM` and `SIGINT` with graceful shutdown.
- Add a `worker` service to `compose.yaml` using the existing backend Dockerfile.

## Explicitly out of scope

- Queue framework selection.
- Queue/job semantics.
- Fake background jobs.
- Business-domain processing.
- Retry/dead-letter design.
- Channel or AI processing.
- New dependencies.

## Acceptance gate

The implementation is acceptable when:

- Python formatting, lint, type checking, and tests pass.
- Compose configuration validates.
- PostgreSQL, Redis, API, Worker, and Web run together in the full stack.
- Worker starts only after required infrastructure is healthy.
- Worker runs as the existing non-root backend user.
- Worker handles Docker `SIGTERM` and exits cleanly.
- Temporary validation workflows are removed from the final diff.
