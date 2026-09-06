# ADR-005: Event-Driven Inbound Message Processing

**Status:** Accepted

## Context

External channels deliver webhooks with limited response windows. AI generation, transcription, document retrieval, external tools, and outbound channel calls may be slow, rate-limited, or temporarily unavailable.

Running the full AI workflow inside the inbound webhook request would increase timeout risk, create poor retry semantics, and tightly couple channel availability to external providers.

## Decision

Inbound channel events use an asynchronous processing architecture.

Preferred flow:

1. Verify/authenticate source.
2. Validate and normalize payload.
3. Resolve tenant/channel context.
4. Enforce supported/enabled message capability.
5. Deduplicate/idempotency check.
6. Persist inbound event/message state.
7. Enqueue processing work.
8. Acknowledge the external webhook promptly.
9. Worker performs conversation/AI/tool/RAG processing.
10. Persist outbound result and execution trace.
11. Dispatch through the channel adapter.

Workers and jobs must tolerate duplicate delivery and controlled retries.

Redis is the initial queue/cache/coordination technology family, while the exact job library is deferred to a later ADR.

## Alternatives Considered

### Fully synchronous webhook processing

Rejected because provider latency or failure would cause webhook timeouts and unsafe channel retries.

### n8n as mandatory message queue/runtime

Rejected for core processing because canonical state, tenant authorization, retry semantics, and domain behavior must remain owned by the platform.

### Kafka from day one

Rejected as premature infrastructure for the initial product scale and operational model.

## Consequences

Positive:

- Fast webhook acknowledgement.
- Controlled retries/fallbacks.
- Worker scaling independent from API processes.
- Better resilience to provider/tool latency.

Trade-offs:

- Processing becomes eventually consistent from webhook receipt to response.
- Idempotency and job state must be modeled carefully.
- Observability needs correlation across HTTP, queue, worker, provider, and outbound delivery.

## Follow-up

- Select the job queue library.
- Define retry/dead-letter policy.
- Define outbound deduplication/idempotency.
- Evaluate transactional outbox for reliable DB-to-queue publication.
