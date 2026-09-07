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
- ADR-009 — Contact Identity and Message Idempotency Boundaries
- ADR-010 — Redis Streams for Channel Job Queue
- ADR-011 — Encrypted Channel Credentials
- ADR-012 — Versioned Prompts and Durable Agent Runs
- ADR-013 — Website Chat Public Session and Polling Transport
- ADR-014 — Canonical Tool Runtime and Structured Agent Tool Loop
- ADR-015 — S3-Compatible Object Storage and PostgreSQL pgvector RAG
- ADR-016 — Gemini-First Provider-Neutral Voice Transcription
- ADR-017 — Conversation Handoff State Machine and Operator Control Boundary
- ADR-018 — Structured Customer Memory and Trust Boundary
- ADR-019 — Deterministic Tenant Policy Engine

Additional ADRs should be created only when a material implementation decision becomes imminent.