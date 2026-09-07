# ADR-019 — Deterministic Tenant Policy Engine

**Status:** Accepted

## Context

Before M13, Customers Manager HUB already enforced several important restrictions application-side:

- channel account capability checks;
- tenant and agent Tool permissions;
- Tool risk/approval rules;
- human-handoff state;
- customer-keyword escalation;
- AI pause while a handoff is active.

Those controls were correct individually, but policy decisions were distributed across modules. The roadmap requires a reusable Policy Engine so deterministic business restrictions can be configured and traced without relying on model compliance or prompt wording.

A general-purpose policy language or external policy service would add disproportionate complexity for the MVP.

## Decision

Implement Policy Engine as a module inside the existing modular monolith.

The policy boundary consists of:

- one optional tenant-level policy configuration;
- optional per-Tool policy overlays;
- deterministic application evaluators;
- append-oriented policy decision traces;
- integration points in the Worker, Tool Runtime, and Human Handoff runtime.

Policy rules are typed configuration, not executable user-authored expressions.

The engine follows a monotonic-security rule:

> A policy may preserve or tighten an existing security restriction, but policy `allow` never bypasses lower-level authorization, capability, validation, credential, tenant-isolation, or built-in high-risk approval controls.

Business-hours calculations use the Python standard-library IANA timezone database through `zoneinfo`; no scheduling service or new infrastructure dependency is introduced.

Existing M11 `HandoffPolicy` configuration remains backward compatible. M13 becomes the preferred source for the overlapping fields while the legacy API is synchronized during the transition.

## Alternatives considered

### Prompt-only policy

Rejected. Models cannot be trusted to enforce critical authorization or autonomy restrictions.

### Generic JSON rule DSL

Rejected for MVP. It would require parser semantics, conflict resolution, versioning rules, security review, and a much larger configuration surface.

### External policy engine/service

Rejected for MVP. A new deployment/runtime dependency is not justified by current scope or scale.

### Hard-coded restrictions only

Rejected. Businesses need tenant-specific behavior for business hours, human routing, message restrictions, and approval strengthening.

## Consequences

Positive:

- critical restrictions are application-enforced;
- policy remains tenant-isolated and auditable;
- existing channel, tool, and handoff boundaries are reused;
- configuration remains understandable for an MVP admin UI;
- no new service or infrastructure component is required.

Trade-offs:

- policy vocabulary is intentionally finite;
- advanced conditional rules require future typed extensions or a later policy-language decision;
- tenant policy revisions must be incremented when configuration or Tool overlays change;
- legacy M11 handoff-policy compatibility must be maintained until that API can be formally deprecated.

## Follow-up implications

- M14 may aggregate PolicyDecisionTrace data for automation/handoff analytics.
- M15 should include policy configuration and trace retention in the security/retention review.
- Any future rule DSL or external policy service requires a new ADR rather than silently expanding this typed configuration into executable expressions.