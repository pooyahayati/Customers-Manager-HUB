# Architecture Decision Records (ADR)

This directory contains durable architecture decisions for Customers Manager HUB.

## Purpose

An ADR records a material technical decision where alternatives existed and the decision has meaningful consequences for implementation, operations, security, or product extensibility.

ADRs should remain concise. They complement `ARCHITECTURE.md`; they do not duplicate it.

## Status Values

- Proposed
- Accepted
- Superseded
- Rejected
- Deprecated

## Naming

Use sequential names:

```text
ADR-001-short-decision-name.md
ADR-002-another-decision.md
```

## Required Sections

Each ADR should include:

- Title
- Status
- Context
- Decision
- Alternatives considered
- Consequences
- Follow-up implications

## Change Rule

Do not silently rewrite an accepted historical decision to pretend it never existed.

If an accepted decision materially changes, create a new ADR that supersedes the previous ADR and update cross-references where appropriate.

## Initial ADR Set

- ADR-001 — Modular Monolith + Independent Workers
- ADR-002 — Provider-Independent AI Gateway + Task Routing
- ADR-003 — Channel Adapters + Canonical Messages
- ADR-004 — Multi-Tenant by Design
- ADR-005 — Event-Driven Inbound Message Processing
- ADR-006 — Defer Self-Hosted Object Storage Backend Selection
- ADR-007 — Separate Admin Console and Customer Webchat
- ADR-008 — Server-Side Admin Sessions and Explicit Tenant Context

Additional ADRs should be created only when a material implementation decision becomes imminent.
