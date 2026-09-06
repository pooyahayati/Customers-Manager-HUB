# ADR-010: Redis Streams for Channel Job Queue

**Status:** Accepted

## Context

ADR-005 requires asynchronous inbound message processing and identifies Redis as the initial queue/cache/coordination technology family, but intentionally deferred the exact queue mechanism.

Milestone 5 introduces the first real channel webhook. The API must acknowledge Telegram promptly, while workers must tolerate duplicate delivery and recover work left pending by a crashed consumer.

## Decision

Use Redis Streams directly through the existing `redis-py` dependency for the initial channel-processing queue.

The channel queue uses one versioned stream and one worker consumer group. Stream entries contain only durable database identifiers and a job type; raw webhook payloads, credentials, media bytes, prompts, and customer message bodies are not placed in Redis.

Delivery semantics are **at least once**:

1. The webhook validates and persists canonical state in PostgreSQL.
2. The API appends a small job entry with `XADD`.
3. Workers consume new entries with `XREADGROUP`.
4. Successful processing is acknowledged with `XACK` and the completed stream entry is deleted.
5. Workers periodically reclaim idle pending entries with `XAUTOCLAIM` so a crashed consumer does not strand work.
6. Channel-processing handlers must therefore be idempotent.

For the initial Telegram flow, if PostgreSQL commit succeeds but Redis enqueue fails, the webhook returns a non-2xx response. Telegram will retry the webhook; deduplication resolves the already-persisted message/event and the retry enqueues the durable database identifier again. This avoids introducing a transactional outbox before evidence justifies it.

No automatic destructive trimming of pending entries is enabled in Milestone 5. Successful entries are deleted after acknowledgment. Dead-letter routing and operational retention policy remain part of production hardening.

## Alternatives Considered

### Redis Lists

Rejected because a simple pop-based worker has weaker crash recovery semantics. A reliable-list pattern would reimplement pending ownership and claiming that Redis Streams already provides.

### Celery, RQ, ARQ, or another job framework

Rejected for the initial channel queue because the required behavior is small, Redis is already a dependency, and adopting a framework would add another runtime abstraction before more job families exist.

### PostgreSQL-only polling queue / transactional outbox immediately

Not selected for Milestone 5. It provides stronger DB-to-queue publication guarantees, but Telegram webhook retry plus durable deduplication gives a simpler recovery path for the first channel. Re-evaluate when non-webhook producers or broader job families require publication independent of an external retry source.

### Kafka

Rejected by ADR-005 as premature for the initial deployment model.

## Consequences

Positive:

- No new queue dependency.
- Worker scaling uses consumer-group semantics.
- Pending work survives worker crashes and can be reclaimed.
- Queue payloads remain small and non-sensitive.
- PostgreSQL remains the source of truth.

Trade-offs:

- Delivery is at least once, so handlers must stay idempotent.
- Redis and PostgreSQL are not one atomic transaction.
- A full dead-letter/retention policy is still required before production hardening is complete.

## Follow-up Implications

- Milestone 5 implements the stream helper, consumer group, acknowledgment, and idle reclaim.
- Milestone 15 must finalize dead-letter strategy, retention, queue observability, and failure runbooks.
- Reconsider a transactional outbox if producers without reliable external retries publish critical jobs.
