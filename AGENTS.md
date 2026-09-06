# AGENTS.md

## 1. Purpose

This file defines the engineering, architecture, security, delivery, and AI-agent rules for **Customers Manager HUB**.

These rules apply to every human contributor and coding agent, including Codex. They are mandatory unless an approved Architecture Decision Record (ADR) explicitly replaces a rule.

The project is intended to become a production-grade, multi-tenant, omnichannel AI customer interaction platform deployable with Docker and suitable for both self-hosted and SaaS operation.

---

## 2. Product Definition

Customers Manager HUB is not a channel-specific chatbot. It is a platform for managing AI-assisted customer interactions across multiple channels.

Core product capabilities include:

- Omnichannel message ingestion and response.
- Configurable AI agents.
- Provider-independent LLM access.
- Task-based AI model routing.
- Tool and external API execution.
- RAG and business knowledge management.
- Customer identity, contacts, conversations, and memory.
- Human handoff.
- Policy enforcement.
- Customer intelligence and communication personalization.
- Audit, observability, usage, cost, and analytics.
- Multi-tenant operation.
- Docker-based deployment.

---

## 3. Engineering Governance

### 3.1 Specification before implementation

No substantial feature should be implemented without a written specification or clearly defined task.

Each implementation task should define, when applicable:

- Goal.
- Scope.
- Out of scope.
- Inputs and outputs.
- Interfaces.
- Data model impact.
- Security considerations.
- Tests.
- Acceptance criteria.

Do not expand scope silently.

### 3.2 Atomic work

Prefer small, reviewable tasks over large generated changes.

A task should not combine unrelated architectural, feature, refactoring, and formatting work.

### 3.3 Architecture decisions

Material architectural decisions must be documented under `docs/adr/`.

Do not contradict an accepted ADR without creating a replacement or superseding ADR.

### 3.4 No speculative infrastructure

Do not introduce infrastructure because it may theoretically be useful later.

Prefer the simplest architecture that satisfies current product and scaling requirements while preserving clear extension points.

---

## 4. Architecture Principles

The following principles are mandatory.

### 4.1 Modular monolith first

The initial backend architecture must be a **modular monolith with independently runnable workers**, not a collection of premature microservices.

Modules must have explicit boundaries and avoid direct coupling to internal implementation details of other modules.

Potential future service extraction must be possible through clear application interfaces and events.

### 4.2 Multi-tenant by design

Tenant isolation is a first-class system invariant.

All tenant-owned domain entities must be tenant-scoped unless there is an explicit architectural reason for a global entity.

Never rely only on frontend filtering for tenant isolation.

Authorization and tenant scoping must be enforced server-side.

Cross-tenant access is forbidden unless explicitly implemented as a privileged platform-level operation.

### 4.3 Provider-independent AI architecture

Business logic must not depend directly on OpenAI-, Google-, or any other provider-specific request/response structures.

Use internal provider abstractions and adapters.

Provider SDKs belong behind adapter boundaries.

Exact model identifiers must be configuration/registry data, not scattered constants in business logic.

### 4.4 Task-based model routing

The platform must support selecting an AI provider and model by task type.

Examples include:

- Customer response generation.
- Voice transcription.
- Intent classification.
- Conversation summarization.
- Memory extraction.
- Image understanding.
- Embedding generation.
- Reranking.
- Complex reasoning/tool use.

A task configuration should be able to define:

- Provider.
- Model identifier.
- Provider/model parameters.
- Timeout.
- Retry policy.
- Fallback chain.
- Cost or quality strategy where supported.

Never implement provider routing as hard-coded conditional logic distributed throughout the application.

### 4.5 Channel-independent core

Telegram, WhatsApp, Instagram, website chat, and future channels must be connectors/adapters.

Channel-specific payloads must be normalized into an internal canonical message contract before entering the conversation/agent core.

The agent runtime must not depend on Telegram-, WhatsApp-, Instagram-, or website-specific payload formats.

### 4.6 Event-driven message processing

Inbound webhooks must not synchronously perform the complete AI workflow.

Preferred flow:

1. Authenticate/validate webhook.
2. Normalize and validate payload.
3. Persist accepted event/message.
4. Enqueue processing work.
5. Return promptly to the channel.
6. Process asynchronously through workers.
7. Persist final state.
8. Dispatch response through the channel adapter.

Idempotency and duplicate event handling are mandatory.

### 4.7 n8n is an integration layer

n8n may be used for external automation and business integration.

It must not become the canonical runtime for core conversation state, tenant authorization, identity, agent orchestration, or primary platform business logic.

---

## 5. Core Domain Boundaries

The initial domain model should preserve clear boundaries for at least:

- Tenant.
- Platform user and tenant membership.
- Role and permission.
- Channel and channel account.
- Contact.
- External identity.
- Conversation.
- Message.
- Agent.
- Prompt and prompt version.
- AI provider/model registry.
- Task model profile/routing policy.
- Tool and tool credential binding.
- Knowledge base and knowledge source.
- Retrieval/RAG records.
- Customer memory.
- Policy.
- Human handoff/assignment.
- Usage and cost records.
- Audit records.

Do not collapse unrelated concepts into generic JSON blobs merely to avoid modeling them.

Use JSON fields only where flexibility is genuinely part of the domain.

---

## 6. Channel Connector Rules

Each channel connector must declare its capabilities rather than assuming all message types are supported.

Possible capabilities include:

- Text receive/send.
- Voice receive/send.
- Audio receive/send.
- Image receive/send.
- Video receive/send.
- Document receive/send.
- Location receive.
- Reactions.
- Buttons/interactive responses.
- Typing indicators.

Tenant configuration must be able to enable or disable allowed inbound message types when the channel supports them.

Connector responsibilities are limited to concerns such as:

- Authentication/webhook verification.
- Channel payload parsing.
- Canonical normalization.
- Media retrieval/upload where required.
- Outbound payload formatting.
- Channel API delivery.

Do not put agent/business decision logic inside channel adapters.

---

## 7. AI Runtime Rules

### 7.1 Prompt stack

Do not model the system as one editable `system_prompt` field.

The runtime must support layered context, conceptually including:

1. Platform policy.
2. Tenant policy.
3. Agent instructions.
4. Channel instructions.
5. Conversation policy/state.
6. Customer context/memory.
7. Retrieved business knowledge.
8. Tool context/results.
9. Current user input.

Trusted platform policy must not be overridable by tenant or user content.

### 7.2 Prompt versioning

Published prompts must be versioned.

For a generated response, the platform should be able to trace which prompt version was used.

Changing a prompt must not destroy the historical configuration used for previous responses.

### 7.3 Structured outputs

Use validated structured outputs for machine-consumed AI results such as:

- Intent.
- Routing decisions.
- Memory extraction.
- Customer intelligence.
- Tool arguments.
- Policy-relevant classifications.

Never rely on parsing arbitrary prose when a schema can be enforced.

### 7.4 AI output is untrusted input

Model outputs must be treated as untrusted data until validated.

Never execute generated SQL, shell commands, URLs, code, or privileged actions without explicit validation and policy controls.

---

## 8. Tool Runtime Rules

Tools must be registered through a canonical tool contract.

A tool definition should support, where applicable:

- Stable name/version.
- Description.
- Input schema.
- Output schema.
- Read/write classification.
- Risk level.
- Authentication binding.
- Timeout.
- Retry policy.
- Tenant scope.
- Agent permissions.
- Confirmation/approval requirement.
- Rate limits.
- Audit behavior.

### 8.1 Credentials

AI models must never receive raw tool credentials.

Credentials must be resolved by trusted server-side infrastructure at execution time.

Secrets must not be placed in prompts, logs, source code, test fixtures, repository files, or client-side configuration.

### 8.2 Read versus write

Read-only and mutating tools must be distinguishable.

High-impact operations such as refunds, destructive changes, sensitive customer modifications, or equivalent actions must support policy enforcement and human approval.

### 8.3 Tool authorization

A model deciding to call a tool is not authorization.

Authorization must be enforced by the application/tool runtime.

---

## 9. Knowledge and RAG Rules

Use RAG for relatively stable knowledge such as:

- Documentation.
- Product guides.
- Policies.
- FAQ.
- Articles.
- Manuals.

Prefer authoritative live tools/APIs for volatile transactional data such as:

- Current prices.
- Inventory.
- Order state.
- Shipment state.
- Customer balance.

Initial vector storage should remain provider-independent.

The initial preferred storage architecture is PostgreSQL with pgvector unless an ADR changes this decision.

Knowledge ingestion must retain useful metadata and source provenance.

Retrieved context must remain tenant-scoped.

---

## 10. Customer Memory and Intelligence

Customer memory must not be treated as unrestricted model-written biography.

Prefer explicit structured memory items.

Where appropriate, distinguish:

- Fact versus inference.
- Source.
- Confidence.
- Creation/update timestamp.
- Expiration or freshness policy.

Avoid inferring sensitive personal traits when not necessary for the business purpose.

Communication personalization should prioritize operationally useful signals such as:

- Preferred language.
- Preferred channel.
- Product interests.
- Lifecycle/lead stage.
- Previous issues and resolutions.
- Response detail preference.
- Relevant unresolved matters.

Do not merge identities automatically without sufficiently reliable evidence and defined policy.

---

## 11. Human Handoff

Human handoff is a core capability, not an optional afterthought.

Conversation state must be explicit and must support AI-active and human-controlled modes.

The application should support escalation based on policy, including cases such as:

- User requests a human.
- Low confidence.
- Repeated failures.
- Complaints.
- Sensitive or high-risk actions.
- Tool failure.
- Configured VIP or priority handling.

AI must not continue sending autonomous replies while a conversation is explicitly under human control unless a policy enables assist-only behavior.

---

## 12. Policy Engine

Deterministic business or safety rules must not exist only as natural-language prompt instructions.

Use application policy controls for enforceable rules such as:

- Tool permission.
- Human approval.
- Channel restrictions.
- Working hours.
- Customer eligibility.
- Sensitive operations.
- Maximum allowed autonomy.

Prompt instructions may influence language and reasoning but are not an authorization boundary.

---

## 13. Security Requirements

Security is part of the Definition of Done.

Minimum requirements include:

- Server-side tenant isolation.
- RBAC/authorization.
- Secure secret storage.
- Webhook authentication/signature or secret verification where supported.
- Input validation.
- Output validation.
- Rate limiting where appropriate.
- Idempotency for inbound external events.
- Safe media/file processing.
- Protection against SSRF in server-side URL access.
- Protection against path traversal and unsafe archive extraction.
- Database parameterization/ORM-safe operations.
- No secrets in logs.
- Auditability for privileged operations.
- Dependency and container security hygiene.

Do not weaken security to make development easier without an explicit approved decision.

---

## 14. Data and Database Rules

Initial primary database: PostgreSQL.

Use schema migrations for all persistent schema changes.

Never modify production schema manually as part of application behavior.

Database rules:

- Use stable primary keys.
- Tenant-owned records require explicit tenant scope.
- Use timestamps consistently.
- Preserve important historical/audit information.
- Add indexes based on actual access patterns.
- Avoid N+1 query patterns.
- Prefer transactions for logically atomic operations.
- Use explicit constraints where the database can enforce domain invariants.

Deletion semantics must be intentional; do not casually hard-delete customer or audit data.

---

## 15. Queue and Worker Rules

Redis may initially be used for queueing, cache, and coordination where appropriate.

Worker jobs must be designed for retries and duplicate delivery.

A retry must not cause duplicate outbound customer messages or duplicate business-side effects.

Long-running work such as AI calls, document ingestion, transcription, and external integration should execute outside inbound webhook request lifecycles.

---

## 16. API Design Rules

Backend APIs must be versionable and have explicit request/response schemas.

Preferred conventions:

- Consistent resource naming.
- Pydantic validation at boundaries.
- Clear HTTP status semantics.
- Stable machine-readable error codes.
- Pagination for collections.
- Authentication and authorization on server-side endpoints.
- Tenant context resolved server-side from authenticated identity or trusted integration context.

Never trust a client-supplied `tenant_id` as authorization by itself.

---

## 17. Observability and Audit

Traditional application logs and AI execution traces serve different purposes and should be modeled accordingly.

A response trace should be able to associate, where permitted:

- Tenant.
- Conversation/message.
- Agent.
- Prompt version.
- AI task.
- Provider/model.
- Retrieval sources.
- Tool calls/results.
- Latency.
- Token/usage metrics.
- Estimated/provider cost where available.
- Errors and retries.

Sensitive content and secrets must be redacted according to policy.

Privileged or mutating operations require auditable records.

---

## 18. Testing Requirements

New production behavior requires tests appropriate to its risk and scope.

Use a combination of:

- Unit tests.
- Integration tests.
- API tests.
- Database/migration tests.
- Connector contract tests.
- Tool authorization tests.
- Tenant isolation tests.
- AI adapter contract tests with mocked provider boundaries where appropriate.

Tests must be deterministic wherever possible.

Do not require paid external AI API calls for the default automated test suite.

Every security-sensitive path must include negative tests for denied/invalid access.

---

## 19. Docker and Deployment Rules

The product must remain deployable through Docker.

Initial deployment target is Docker Compose for self-hosted installation.

Expected logical components may include:

- Web/frontend.
- API/backend.
- Worker.
- Scheduler/background jobs where required.
- PostgreSQL + pgvector.
- Redis.
- S3-compatible object storage such as MinIO for self-hosted deployments.
- Reverse proxy.
- Optional observability stack.

Backend and worker may share the same application image/codebase when appropriate.

Containers should:

- Run as non-root where practical.
- Have health checks where useful.
- Use environment-driven configuration.
- Avoid baking secrets into images.
- Use reproducible dependency versions.

---

## 20. Environment Configuration

Real credentials must never be committed.

The repository should provide `.env.example` containing non-secret examples and documentation.

Configuration must clearly distinguish:

- Required values.
- Optional values.
- Development defaults.
- Production-sensitive settings.

A missing critical production configuration should fail clearly rather than silently using an insecure fallback.

---

## 21. Recommended Technology Direction

Unless superseded by an ADR, the initial direction is:

- Backend: Python + FastAPI.
- Validation: Pydantic.
- ORM/database layer: SQLAlchemy.
- Database: PostgreSQL.
- Vector search: pgvector initially.
- Queue/cache: Redis.
- Frontend: Next.js + TypeScript.
- Object storage: S3-compatible API / MinIO for self-hosted installations.
- Deployment: Docker + Docker Compose.
- Observability architecture: OpenTelemetry-compatible instrumentation.

Libraries and exact versions must be selected deliberately during implementation, not guessed from stale examples.

---

## 22. Coding Standards

### Python

- Use type hints for public/internal application interfaces.
- Prefer small cohesive functions and classes.
- Keep domain/business logic outside HTTP route handlers.
- Keep provider SDK logic inside adapters.
- Avoid hidden global mutable state.
- Use async I/O only where it provides actual benefit and keep async boundaries consistent.
- Raise/use explicit domain or application errors rather than generic exceptions for expected failure modes.

### TypeScript

- Enable strict type checking.
- Avoid `any` unless justified.
- Keep API schemas/types synchronized through a deliberate contract strategy.
- Do not place secrets or privileged logic in the browser.

### General

- Prefer clarity over cleverness.
- Avoid dead code and speculative abstractions.
- Do not duplicate domain rules across modules.
- Do not swallow exceptions silently.
- Do not log secrets or raw credentials.

---

## 23. Repository and Git Rules

The default branch should remain stable.

Use scoped branches for implementation work.

Recommended branch prefixes:

- `feat/`
- `fix/`
- `chore/`
- `docs/`
- `refactor/`
- `test/`

Commits should be small enough to review and use meaningful messages.

Do not rewrite shared history without explicit approval.

Do not mix unrelated changes into a pull request.

Every meaningful PR should explain:

- What changed.
- Why.
- Risks.
- Tests performed.
- Migration/deployment impact where applicable.

---

## 24. Codex Working Rules

Codex acts as an implementation engineer under project architecture and task specifications.

Codex must:

1. Read `AGENTS.md` before implementation.
2. Read relevant PRD, architecture, ADR, and task/spec files.
3. Inspect existing code before modifying it.
4. Make only the changes necessary for the assigned task.
5. Preserve tenant, security, and module boundaries.
6. Add/update tests for behavior changes.
7. Run relevant tests/lint/type checks when available.
8. Report assumptions and unresolved risks.
9. Avoid broad refactors unless the task requires them.
10. Never invent credentials, endpoints, provider model IDs, or business rules.

Codex must not independently change core architecture merely because a different implementation appears easier.

If a task conflicts with this file or an accepted ADR, stop implementation and surface the conflict.

---

## 25. Forbidden Patterns

Unless explicitly approved, do not:

- Couple core business logic directly to a specific AI provider.
- Couple the agent runtime directly to a channel SDK.
- Put secrets in source code or prompts.
- Allow an LLM tool call to bypass application authorization.
- Store volatile transactional truth only in RAG.
- Treat generated AI output as trusted executable instructions.
- Make frontend tenant filtering the security boundary.
- Introduce Kafka, Kubernetes, or microservices without demonstrated need and an ADR.
- Use n8n as the canonical database/state machine for the core platform.
- Add a large agent framework as the owner of the domain model without an explicit ADR.
- Auto-merge customer identities from weak model inference.
- Let AI continue autonomous outbound replies during explicit human takeover unless policy permits it.
- Commit `.env` files containing real secrets.

---

## 26. Definition of Done

A feature is not complete merely because code compiles or a happy-path demo works.

Depending on scope, Done requires:

- Acceptance criteria satisfied.
- Correct module boundaries.
- Tenant isolation preserved.
- Authorization enforced.
- Validation and error handling implemented.
- Relevant tests passing.
- Database migration included if needed.
- API/schema documentation updated if needed.
- Observability/audit requirements addressed.
- No secret leakage.
- Docker/deployment impact considered.
- Documentation/ADR updated when architecture changes.
- Code review completed.
- Product-owner/user approval where the project workflow requires it.

---

## 27. Change Control

This file is intentionally strict because it protects the maintainability and commercial viability of the platform.

Changes to these rules should be deliberate, reviewed, and committed separately from unrelated feature work.
