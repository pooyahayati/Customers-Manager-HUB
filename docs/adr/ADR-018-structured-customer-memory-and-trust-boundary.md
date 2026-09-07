# ADR-018 — Structured Customer Memory and Trust Boundary

## Status

Accepted

## Context

Milestone 12 requires durable customer personalization across conversations. Treating memory as a free-form model-written biography would make provenance, correction, expiry, tenant isolation, and fact/inference safety difficult to enforce. The platform already has a canonical Contact/Conversation model and provider-independent AI task routing, including `customer_memory_extraction`.

## Decision

Customer memory will be stored as structured, tenant-scoped memory items attached to a Contact.

Each item carries a constrained category, text value, fact/inference evidence kind, confidence, provenance, observation/freshness metadata, verification metadata, and soft-deletion metadata.

The model may only emit an allowlisted set of operational categories. Model-generated inference remains explicitly labeled as inference and is not eligible for autonomous personalization until a tenant operator verifies it. Customer-stated facts may be selected automatically when fresh and above the configured runtime confidence threshold.

Memory extraction uses the existing AI Gateway task routing and runs as best-effort enrichment inside the existing queued worker lifecycle after the primary customer-service outcome. A small per-source-message extraction marker makes the operation idempotent without introducing a new queue or service.

Agent Runtime selects bounded, fresh memory context from PostgreSQL and inserts it as untrusted customer context. Memory never becomes platform policy or authorization input.

## Alternatives Considered

### Free-form customer profile JSON

Rejected because it obscures provenance, makes partial correction/deletion difficult, and encourages unrestricted model-authored biography.

### Vector database for customer memory

Rejected for M12. The required signals are small, structured, contact-scoped, and efficiently queried from PostgreSQL. Vector search would add unnecessary infrastructure and weaker deterministic filtering.

### Separate memory microservice or dedicated queue

Rejected for M12. The modular monolith and current Redis-backed worker already provide the required execution boundary. A new service/queue would add operational complexity without a demonstrated scaling need.

### Require human approval for every extracted fact

Rejected because it would make basic returning-customer personalization operationally expensive. Explicit customer-stated facts can be used with provenance/confidence safeguards, while model inference retains a stricter verification boundary.

## Consequences

- Memory remains auditable and correctable at item level.
- Fact and inference cannot silently collapse into the same trust level.
- Freshness filtering is deterministic and application-controlled.
- Returning-customer context can be reused across Telegram and Website Chat without channel-specific memory logic.
- PostgreSQL remains the source of truth and no new infrastructure is required.
- The memory model intentionally does not support unrestricted arbitrary sensitive profiling.

## Follow-up Implications

- M13 Policy Engine may add tenant-level controls for memory eligibility, retention, or category use.
- M15 Production Hardening should define retention/privacy procedures for customer memory.
- M14 Analytics may measure memory extraction/usage outcomes without exposing sensitive content.
