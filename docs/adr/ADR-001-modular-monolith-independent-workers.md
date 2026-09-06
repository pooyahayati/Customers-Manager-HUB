# ADR-001: Modular Monolith with Independent Workers

**Status:** Accepted

## Context

The product needs strong transactional consistency, rapid iteration, simple Docker deployment, and clear module boundaries. Beginning with microservices would add operational overhead before independent scaling or team ownership requirements are proven.

At the same time, webhook handling, AI calls, transcription, document ingestion, and external tools require asynchronous execution outside request lifecycles.

## Decision

Use a modular monolith for the initial backend, with independently runnable worker processes that may share the same codebase and container image.

The API process handles HTTP/API/webhook responsibilities and short request work. Workers handle long-running and retryable jobs.

Modules must expose explicit application/domain interfaces and avoid coupling through another module's persistence internals.

## Alternatives Considered

### Microservices from day one

Rejected because it adds service discovery, distributed tracing, deployment complexity, cross-service transactions, versioned contracts, and operational burden before scale demonstrates a need.

### Single synchronous application process

Rejected because inbound webhooks and user requests must not block on AI providers, transcription, RAG ingestion, or slow external tools.

## Consequences

Positive:

- Simpler development and deployment.
- Easier transactions and local debugging.
- Faster product iteration.
- Clear path to horizontal worker scaling.

Trade-offs:

- Internal module discipline is required to prevent a tightly coupled monolith.
- Future extraction may require explicit service contracts when justified.

## Follow-up

- Define the Python package/module boundaries before feature implementation.
- Select the queue/job technology through a later ADR.
- Extract services only based on demonstrated scale, reliability, or ownership needs.
