# Product & Engineering Roadmap

## Customers Manager HUB

**Status:** Draft v0.1  
**Delivery model:** Milestone-based, specification-first, review-gated

---

## 1. Roadmap Rules

This roadmap defines sequence and scope, not calendar promises.

Every milestone must pass its acceptance gate before the next major capability becomes the implementation focus.

Rules:

1. Architecture and tenant isolation take priority over feature count.
2. Telegram + Website Chat are the first channel targets.
3. OpenAI + Gemini are the first AI providers.
4. AI models are selected through task profiles, not hard-coded globally.
5. Long-running work must use workers/queues.
6. Human handoff is part of the core conversation lifecycle.
7. n8n remains optional integration infrastructure, not the source of truth.
8. Every implementation task follows `AGENTS.md`.
9. Material architecture choices require ADRs.
10. A milestone is not complete until tests, security, documentation, and Docker impact are addressed.

---

## Milestone 0 — Project Governance & Bootstrap

### Goal

Create the project contract before application code begins.

### Deliverables

- `AGENTS.md`
- `PRD.md`
- `ARCHITECTURE.md`
- `ROADMAP.md`
- `README.md`
- `.env.example`
- `.gitignore`
- `.dockerignore`
- Initial `docker-compose.yml`
- ADR directory and initial ADRs
- Development branch workflow

### Exit Gate

- Product scope is documented.
- Architecture invariants are documented.
- Initial infrastructure assumptions are documented.
- No real secrets exist in Git.

---

## Milestone 1 — Repository & Runtime Skeleton

### Goal

Create a runnable development skeleton without product features.

### Scope

- `apps/api` FastAPI application skeleton.
- `apps/web` Next.js application skeleton.
- Backend worker entry point.
- Python dependency/project management decision.
- TypeScript/Node package management decision.
- Configuration loader.
- Structured logging foundation.
- Health/readiness endpoints.
- Dockerfiles.
- Docker Compose application services.
- PostgreSQL connectivity.
- Redis connectivity.
- MinIO/S3 connectivity abstraction.
- Migration infrastructure.
- Test infrastructure.
- Lint/type-check infrastructure.

### Exit Gate

A clean clone can be configured from `.env.example` and started through the documented development workflow.

---

## Milestone 2 — Tenant, Authentication & RBAC Foundation

### Goal

Establish the security boundary before tenant-owned product features.

### Scope

- Tenant entity/lifecycle foundation.
- Platform user.
- Tenant membership.
- Roles and permissions.
- Authentication/session strategy.
- Server-side tenant context.
- Authorization helpers/policies.
- Audit foundation for privileged configuration changes.
- Tenant isolation tests.

### Exit Gate

- Authenticated user can access only authorized tenants.
- Cross-tenant negative tests pass.
- Privileged actions are auditable.

---

## Milestone 3 — Contacts, Identities, Conversations & Messages

### Goal

Create the canonical customer interaction domain independent of AI and channels.

### Scope

- Contact.
- External identity.
- Conversation.
- Message.
- Attachment/media metadata.
- Conversation state foundation.
- Contact/conversation APIs.
- Idempotency primitives for external messages.
- Customer identity resolution rules.

### Exit Gate

The platform can persist and retrieve tenant-scoped customer conversation history without any AI provider dependency.

---

## Milestone 4 — AI Gateway & Task Model Routing

### Goal

Create provider-independent AI infrastructure.

### Scope

- AI provider adapter contract.
- Provider/model registry.
- AI task taxonomy.
- Task model profiles.
- OpenAI adapter.
- Gemini adapter.
- Retry/fallback handling.
- Structured-output contract.
- Usage normalization.
- AI execution trace foundation.
- Mock provider for deterministic tests.

### Required task profiles

- Customer response.
- Voice transcription.
- Intent classification.
- Conversation summary.
- Customer memory extraction.
- Embedding.

### Exit Gate

The same application-level AI operation can be routed through configured providers/models without business modules importing provider SDKs.

---

## Milestone 5 — Channel Framework & Telegram

### Goal

Prove the canonical channel architecture with a real external channel.

### Scope

- Channel definitions/configuration.
- Channel account.
- Capability registry.
- Allowed inbound message-type configuration.
- Canonical inbound message schema.
- Telegram webhook verification.
- Telegram text ingestion.
- Telegram voice ingestion/media retrieval.
- Message deduplication.
- Queue-based processing.
- Telegram outbound text delivery.
- Connector contract tests.

### Exit Gate

Telegram text and voice messages enter the canonical conversation model and outbound responses can be dispatched without Telegram-specific logic inside the AI/conversation core.

---

## Milestone 6 — Agent & Prompt Runtime

### Goal

Create configurable AI behavior for customer conversations.

### Scope

- Agent entity/configuration.
- Agent assignment/routing foundation.
- Prompt definitions.
- Prompt versioning.
- Draft/publish lifecycle.
- Layered prompt composer.
- Conversation context builder.
- AI customer-response orchestration.
- Output validation.
- Response persistence and dispatch.

### Exit Gate

A tenant can configure and publish an agent/prompt and use it to answer a Telegram text conversation through the configured customer-response task model.

---

## Milestone 7 — Website Chat Channel

### Goal

Prove that the core is genuinely omnichannel.

### Scope

- Website channel configuration.
- Anonymous/known visitor session design.
- Embeddable chat widget foundation.
- Website inbound/outbound transport.
- Canonical conversation reuse.
- Tenant/site origin security policy.

### Exit Gate

Telegram and Website Chat use the same conversation/agent runtime without duplicated AI business logic.

---

## Milestone 8 — Tool Runtime & Live Business Data

### Goal

Allow agents to safely use business capabilities.

### Scope

- Tool registry.
- Input/output schema validation.
- Read/write classification.
- Risk levels.
- Credential binding.
- Tenant/agent authorization.
- Generic REST API tool adapter.
- Product/business API reference tool.
- Google Sheets integration path.
- Tool execution trace/audit.
- Timeout/retry policy.
- Human approval foundation for high-risk operations.

### Exit Gate

An agent can request an approved tool, the application independently authorizes and executes it, and no raw credential is exposed to the model.

---

## Milestone 9 — Knowledge Base & RAG

### Goal

Add tenant-owned business knowledge without provider lock-in.

### Scope

- Knowledge base/source entities.
- Object storage upload.
- PDF ingestion.
- Excel ingestion.
- Parsing/normalization.
- Chunking and metadata.
- Embedding task routing.
- pgvector storage.
- Tenant-scoped retrieval.
- Source provenance.
- Optional reranking interface.
- Retrieval traces.

### Exit Gate

An agent can answer from uploaded tenant knowledge while retaining traceable source references and without leaking knowledge across tenants.

---

## Milestone 10 — Voice Transcription End-to-End

### Goal

Complete configured voice-to-text processing.

### Scope

- Voice media storage/reference lifecycle.
- Transcription worker job.
- `voice_transcription` task profile resolution.
- Configurable Gemini/OpenAI/future transcription adapters.
- Transcript persistence.
- Failure/fallback behavior.
- Continue normal agent pipeline after transcription.

### Exit Gate

A Telegram voice message is transcribed using the tenant's configured provider/model and answered through the normal conversation runtime.

---

## Milestone 11 — Human Handoff & Operator Inbox

### Goal

Make the platform operationally safe for real customer service.

### Scope

- Conversation handoff state machine.
- Human queue.
- Assignment/claim/release.
- Operator conversation view.
- Human outbound replies through originating channel.
- AI autonomy pause/resume.
- AI assist-only suggestions.
- Escalation rules.

### Exit Gate

A conversation can move safely between autonomous AI and human control without duplicate or competing responses.

---

## Milestone 12 — Customer Memory & Intelligence

### Goal

Create structured personalization and customer-context capabilities.

### Scope

- Customer memory entity.
- Fact/inference distinction.
- Provenance/confidence/freshness.
- Memory extraction task.
- Customer context selection.
- Preferred language/detail level.
- Product interests.
- Lead/lifecycle status foundation.
- Previous issues/resolutions.
- Admin visibility/edit/delete controls where appropriate.

### Exit Gate

Returning customers can receive approved contextual personalization without treating arbitrary model inference as verified fact.

---

## Milestone 13 — Policy Engine

### Goal

Move deterministic restrictions out of prompt-only enforcement.

### Scope

- Policy model/evaluation foundation.
- Tool allow/deny rules.
- Approval rules.
- Message capability rules.
- Human-handoff triggers.
- Business-hours behavior.
- AI autonomy rules.
- Policy audit/trace.

### Exit Gate

Critical business permissions and autonomy restrictions are enforced application-side regardless of model output.

---

## Milestone 14 — Analytics, Usage & Cost

### Goal

Make product value and AI cost measurable.

### Scope

- Per-tenant AI usage.
- Per-provider/model metrics.
- Token/billable-unit tracking.
- Cost calculation/normalization foundation.
- Conversation counts.
- Unique contacts.
- First response time.
- Resolution time.
- Human handoff rate.
- AI automation rate.
- Tool success/failure metrics.
- Admin dashboard foundation.

### Exit Gate

A tenant can see both operational performance and AI consumption/cost indicators.

---

## Milestone 15 — Security & Production Hardening

### Goal

Prepare the MVP for controlled production deployment.

### Scope

- Security review.
- Rate limiting.
- Secret handling review.
- File-processing hardening.
- SSRF controls.
- Dependency/container scanning workflow.
- Backup/restore procedure.
- Database migration/upgrade procedure.
- Health/readiness checks.
- Data retention foundation.
- Observability completion.
- Failure/retry/dead-letter strategy.
- Load tests for critical paths.

### Exit Gate

Security, restore, upgrade, and operational runbooks are documented and tested for the supported deployment topology.

---

## Milestone 16 — Commercial MVP Validation

### Goal

Validate the product as a reusable business platform rather than a custom deployment.

### Required end-to-end scenario

A fresh installation must be able to:

1. Start from documented Docker setup.
2. Create a tenant.
3. Configure users/roles.
4. Configure OpenAI/Gemini credentials.
5. Select models by AI task.
6. Connect Telegram.
7. Configure Website Chat.
8. Enable text/voice channel capabilities.
9. Upload PDF/Excel knowledge.
10. Configure a live business API tool.
11. Configure/publish an agent and prompt.
12. Receive text and voice messages.
13. Use RAG and live tools appropriately.
14. Respond through the originating channel.
15. Escalate to a human.
16. Preserve contacts/conversations/memory.
17. Display trace, audit, usage, and core analytics.

### Exit Gate

No tenant-specific source-code fork is required to demonstrate the complete supported scenario.

---

## Post-MVP Candidates

Priority will be determined from customer validation and operational data.

Candidate areas:

- WhatsApp native connector.
- Instagram native connector.
- Additional channels.
- Phone/voice-call agent.
- Advanced CRM integrations.
- Advanced tool marketplace/plugin system.
- Campaign/broadcast capabilities.
- Advanced routing and multiple specialist agents.
- SLA/workforce management.
- Advanced customer journey analytics.
- Subscription/billing/quota enforcement.
- White-label customization.
- Managed SaaS deployment automation.
- High-scale service extraction where justified.

---

## Roadmap Change Control

A material scope or sequencing change should update this roadmap and, where relevant, `PRD.md` or an ADR before implementation proceeds.
