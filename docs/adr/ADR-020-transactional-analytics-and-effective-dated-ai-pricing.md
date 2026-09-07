# ADR-020 — Transactional Analytics and Effective-Dated AI Pricing

**Status:** Accepted for M14

## Context

By M13 the platform already persists the operational records required for the MVP analytics surface: AI execution traces, conversations/messages, human handoffs, agent runs, and tool executions.

Creating a parallel analytics event pipeline now would duplicate facts, add delivery/idempotency concerns, and increase operational complexity before production scale demonstrates a need for it.

AI cost cannot be inferred safely from provider/model identifiers alone because rates change over time and may differ by tenant contract.

## Decision

M14 will use the existing transactional PostgreSQL records as the analytics source of truth and perform bounded report-time aggregation.

A new tenant-scoped `ai_model_pricing` table will store effective-dated rates for provider/model combinations.

Pricing is configuration supplied by the tenant administrator. Vendor prices are not hard-coded or scraped.

Cost estimates resolve the most recent rate where `effective_from <= AIExecutionTrace.created_at` and aggregate reported token/audio consumption using decimal arithmetic.

The reporting API will expose aggregate counts, timing metrics, rates, billable units, estimated cost, and explicit unpriced usage. It will not expose message text, prompts, credentials, tool payloads, or memory values.

## Consequences

### Positive

- avoids a premature warehouse/event pipeline;
- uses already-tested tenant-scoped canonical facts;
- historical price changes are representable without rewriting execution traces;
- tenant-specific provider contracts are supported;
- unpriced usage remains visible instead of silently becoming zero or guessed cost;
- the design can later be backed by rollups/materialized views without changing the API contract.

### Trade-offs

- report-time aggregation is not intended for unbounded historical scans;
- conversation resolution time initially uses the existing conversation lifecycle timestamps rather than a new event ledger;
- the first dashboard remains intentionally lightweight because the current admin web application does not yet contain a full authentication/session UX.

## Guardrails

1. Every analytics query is tenant scoped.
2. Report windows are bounded.
3. Pricing mutation is Owner/Admin only and auditable.
4. No provider price is treated as authoritative unless configured in application data.
5. Missing pricing produces explicit unpriced usage.
6. Analytics cannot read or return raw sensitive business/customer payloads.
7. No new analytics queue/service is introduced in M14.

## Revisit criteria

Reconsider direct aggregation when measured production workloads show unacceptable query latency, retention requirements exceed practical transactional scans, or cross-tenant platform analytics require a dedicated warehouse.