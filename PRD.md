# Product Requirements Document (PRD)

## Customers Manager HUB

**Status:** Draft v0.1  
**Target:** Initial commercial MVP  
**Architecture direction:** Multi-tenant, omnichannel, provider-independent AI customer interaction platform  
**Deployment target:** Docker / Docker Compose, with a path to SaaS operation

---

## 1. Executive Summary

Customers Manager HUB is a configurable AI-powered customer interaction platform designed for businesses that need to receive, understand, answer, route, and analyze customer conversations across multiple communication channels.

The product must support multiple AI providers, configurable model selection by task, business knowledge/RAG, external tools and APIs, customer memory, human handoff, analytics, auditability, and multi-tenant isolation.

The system is not intended to be a simple chatbot or a channel-specific bot. It should operate as a reusable platform that can be installed for one business or offered as a commercial SaaS product for many businesses.

---

## 2. Product Vision

Enable a business to configure an AI customer-service and sales operation from an administration panel without hard-coding its business logic into individual channel integrations.

A business should be able to:

- Connect communication channels.
- Choose which inbound message types are accepted per channel.
- Configure one or more AI agents.
- Select AI provider/model per task.
- Connect live business APIs and tools.
- Upload and maintain business knowledge.
- Define system prompts, policies, and response behavior.
- View contacts and conversation history.
- Transfer conversations to human agents.
- Build customer context and communication preferences over time.
- Measure AI effectiveness, cost, and business outcomes.
- Deploy the complete system through Docker.

---

## 3. Product Principles

The following product principles are mandatory.

### 3.1 Omnichannel, not channel-specific

Telegram, website chat, WhatsApp, Instagram, and future channels are connectors around a common conversation core.

### 3.2 AI-provider independent

The platform must support multiple AI providers through adapters. Business logic must not be locked to one provider SDK.

### 3.3 Task-based AI model selection

A tenant must be able to select a different provider/model for different AI tasks.

Examples:

- Customer response generation.
- Voice-to-text transcription.
- Intent classification.
- Conversation summarization.
- Customer memory extraction.
- Image understanding.
- Embeddings.
- Reranking.
- Complex reasoning/tool execution.

### 3.4 Multi-tenant by design

All customer/business data must be isolated by tenant at the backend level.

### 3.5 Human control remains available

The product must support AI autonomy, assist-only operation, and explicit human takeover.

### 3.6 Business truth should come from authoritative sources

Live transactional values such as price, stock, order state, and shipment state should come from tools/APIs rather than stale RAG content.

### 3.7 Auditable AI

The platform should make important AI decisions traceable, including model, prompt version, retrieved knowledge, tool calls, latency, and cost/usage.

---

## 4. Primary Users

### 4.1 Platform Owner

Operates the commercial product itself.

Responsibilities may include:

- Tenant management.
- Platform configuration.
- Global policies.
- Provider availability.
- Platform-level usage monitoring.
- Support and troubleshooting.

### 4.2 Tenant Owner / Business Owner

Owns a business workspace.

Needs to:

- Configure business identity and settings.
- Manage channels.
- Manage AI configuration.
- Manage business users.
- Manage knowledge and tools.
- Review analytics and usage.

### 4.3 Tenant Administrator

Manages day-to-day system configuration within a tenant.

### 4.4 Supervisor

Monitors conversations, escalations, agent quality, and human support performance.

### 4.5 Human Support/Sales Agent

Handles assigned conversations and may use AI-assisted suggestions.

### 4.6 Analyst / Viewer

Reads reports, conversation analytics, and operational metrics without privileged modification rights.

### 4.7 End Customer

Communicates with the business through Telegram, website chat, or future connected channels.

---

## 5. MVP Scope

The first commercial MVP should prioritize a stable architecture and a complete customer-interaction lifecycle instead of supporting every possible channel or integration.

### 5.1 Included in MVP

- Multi-tenant foundation.
- User authentication and RBAC.
- Telegram connector.
- Website chat connector/widget.
- Text message support.
- Voice message intake and transcription.
- OpenAI provider adapter.
- Gemini provider adapter.
- Task-based model configuration.
- Provider/model fallback configuration.
- Agent configuration.
- Layered prompt configuration and prompt versioning.
- Generic REST API tool integration.
- Structured product/business API tool pattern.
- Google Sheets integration or tool adapter.
- PDF knowledge ingestion.
- Excel knowledge ingestion.
- RAG with provider-independent vector storage.
- Contact management.
- Channel identity mapping.
- Conversation and message history.
- Customer memory/intelligence foundation.
- Human handoff.
- Basic policy rules.
- AI execution traces.
- Usage and cost tracking foundation.
- Basic operational/business analytics.
- Audit log.
- Docker Compose deployment.

### 5.2 Explicitly deferred from MVP

Unless required by an implementation dependency, the following should not block MVP delivery:

- Native WhatsApp connector.
- Native Instagram connector.
- Facebook Messenger.
- LinkedIn.
- Voice phone calls.
- Full campaign/broadcast marketing automation.
- Complex CRM replacement features.
- Kubernetes deployment.
- Microservice decomposition.
- Advanced billing/payment collection.
- Marketplace for third-party tools.
- Fully autonomous workflow builder.
- Advanced psychographic/personality profiling.

Deferred features must remain architecturally possible without contaminating MVP scope.

---

## 6. Core User Journeys

### 6.1 Connect Telegram

1. Tenant administrator opens Channels.
2. Selects Telegram.
3. Enters required bot credentials/configuration.
4. System validates configuration.
5. Administrator selects allowed inbound message types.
6. Channel becomes active.
7. Incoming Telegram messages are normalized into the platform conversation model.

### 6.2 Connect Website Chat

1. Administrator creates or configures a website channel.
2. System provides an embeddable website chat configuration/script.
3. Website visitors can start conversations.
4. Messages enter the same conversation engine used by other channels.

### 6.3 Configure AI Tasks

1. Administrator opens AI Settings.
2. Selects a task such as Customer Response or Voice Transcription.
3. Selects provider.
4. Selects/configures model identifier and parameters.
5. Optionally defines fallback providers/models.
6. Saves the task profile.
7. Runtime resolves models through the task-routing layer.

Example configuration concept:

- Customer Response → OpenAI → selected high-quality response model.
- Voice Transcription → Gemini → selected transcription-capable model.
- Intent Classification → lower-cost model.
- Memory Extraction → lower-cost structured-output model.

Exact model identifiers are configuration data and must not be hard-coded throughout business logic.

### 6.4 Answer Using Business Knowledge

1. Administrator uploads PDF/Excel business knowledge.
2. System processes, chunks, indexes, and stores provenance.
3. Customer asks a related question.
4. Agent retrieves tenant-scoped relevant knowledge.
5. Agent produces a response based on the retrieved evidence.
6. Trace records relevant retrieval information.

### 6.5 Answer Using Live Business API

1. Customer asks for current price, stock, order status, or another volatile value.
2. Agent identifies need for authoritative live data.
3. Tool runtime validates tool permission.
4. Tool runtime resolves credentials server-side.
5. External API is called.
6. Structured result is returned to the agent.
7. Agent composes customer-facing response.
8. Tool call is auditable.

### 6.6 Human Handoff

1. AI or customer triggers escalation.
2. Conversation changes to a human-handoff state.
3. Qualified human users can see and claim/receive the conversation.
4. AI autonomous replies stop while human control is active unless assist-only mode is explicitly enabled.
5. Human may return the conversation to AI after resolution or intervention.

### 6.7 Returning Customer Personalization

1. Customer returns through a known external identity.
2. System resolves the contact.
3. Relevant non-sensitive customer context and prior interaction data are loaded.
4. AI response is adapted using approved customer context.
5. New structured memory may be extracted after the conversation subject to policy.

---

## 7. Functional Requirements

### FR-001 Tenant Management

The platform must support multiple isolated tenants.

Each tenant should have its own:

- Users/memberships.
- Channels.
- Agents.
- Prompts.
- AI task model configurations.
- Tools and credentials.
- Knowledge bases.
- Contacts.
- Conversations/messages.
- Policies.
- Memory/customer intelligence.
- Usage data.

### FR-002 User Management and RBAC

The system must support authenticated platform users and tenant memberships.

Initial role concepts should include:

- Owner.
- Admin.
- Supervisor.
- Agent.
- Analyst.
- Viewer.

Permissions must be enforced server-side.

### FR-003 Channel Management

The administration panel must allow channels to be configured and enabled/disabled.

Each connector must declare supported capabilities.

Tenant administrators must be able to allow/disallow supported inbound content types, including at minimum for MVP:

- Text.
- Voice.

The architecture should permit future content types such as image, video, document, audio, location, and reactions.

### FR-004 Canonical Message Model

All inbound channel payloads must be normalized before entering core conversation processing.

The canonical model must preserve enough external identifiers and metadata for:

- Deduplication/idempotency.
- Contact identity resolution.
- Conversation association.
- Response routing.
- Audit/debugging.

### FR-005 Contact Management

The system must maintain contacts independently from channel-specific identities.

A contact may have multiple external identities, for example Telegram, website session/account, WhatsApp, Instagram, email, phone, or CRM identifiers.

Identity merge must require reliable evidence and policy.

### FR-006 Conversation Management

The platform must store conversation threads and individual messages.

Conversation state must support AI operation and human takeover.

The system must preserve historical records needed for support, analytics, and traceability.

### FR-007 AI Provider Registry

The platform must support multiple AI providers through adapters.

Initial providers:

- OpenAI.
- Google Gemini.

Provider-specific credentials must be tenant- or platform-configurable according to deployment policy.

### FR-008 AI Task Routing

The system must allow a provider/model configuration per AI task.

A task profile should support:

- Provider.
- Model ID.
- Model/provider parameters.
- Timeout.
- Retry behavior.
- Fallback chain.
- Optional cost/quality strategy metadata.

### FR-009 Agent Management

A tenant must be able to create/configure one or more agents.

Agent configuration should include, at minimum:

- Name.
- Purpose/role.
- Prompt assignment.
- Allowed knowledge bases.
- Allowed tools.
- Channel applicability where relevant.
- Handoff behavior.
- Enabled/disabled state.

### FR-010 Prompt Management

Prompts must support versioning.

The system must support layered prompt/context composition rather than only one mutable prompt field.

Historical messages should retain enough references to identify the prompt version used.

### FR-011 Tool Registry and Execution

The system must support external tools with schema-based input/output contracts.

Tools must support authorization and risk metadata.

The AI model must never receive raw credentials.

The application must independently authorize tool calls before execution.

### FR-012 Knowledge Base

Tenant users must be able to create knowledge bases and add sources.

MVP source types:

- PDF.
- Excel.

The architecture must allow future sources such as web pages, articles, documents, Google Drive, databases, and APIs.

### FR-013 RAG

The system must support tenant-scoped retrieval.

Initial preferred vector implementation is PostgreSQL + pgvector.

Stored chunks must retain source metadata/provenance.

### FR-014 Voice Processing

MVP must accept voice messages from supported channels.

Voice content must be processed through the configured transcription task profile.

Transcribed text must enter the normal message/agent pipeline while retaining reference to the original voice media and transcription metadata.

### FR-015 Human Handoff

Conversation state must support at least:

- AI active.
- Human required.
- Waiting for human.
- Human active.
- Waiting for customer.
- Resolved.
- Closed.

Exact naming may evolve during domain modeling, but equivalent capabilities are required.

### FR-016 Policy Controls

The application must support deterministic policy enforcement separate from prompts.

MVP policies should cover at least:

- Tool permission.
- Human handoff triggers.
- Channel/message-type restrictions.
- AI autonomous/human-controlled state.

### FR-017 Customer Memory and Intelligence

The product must support structured customer-context records.

Where appropriate records should distinguish:

- Fact vs inference.
- Source.
- Confidence.
- Created/updated timestamps.
- Expiration/freshness.

Useful initial attributes may include:

- Preferred language.
- Product interests.
- Lead/lifecycle stage.
- Prior issues and resolutions.
- Response detail preference.
- Open/unresolved topics.

### FR-018 Traceability

For AI-generated responses, the system should be able to associate relevant execution information such as:

- Conversation/message.
- Agent.
- Prompt version.
- AI task.
- Provider/model.
- Retrieved sources.
- Tool calls.
- Latency.
- Usage/tokens where available.
- Cost estimate/provider cost where available.
- Errors/retries.

### FR-019 Analytics

MVP should provide a foundation for both operational/business analytics and AI analytics.

Initial metrics may include:

Business/operations:

- Conversation count.
- Unique contacts.
- First response time.
- Resolution time.
- Human handoff rate.
- AI automation rate.

AI/platform:

- Provider/model usage.
- Token/usage totals where available.
- Estimated/provider cost.
- AI latency.
- Tool success/failure rate.
- Error rate.

### FR-020 Audit Log

Privileged configuration changes and mutating business operations must be auditable.

Audit records should identify actor, tenant, action, target, time, and relevant safe metadata.

### FR-021 Docker Deployment

The MVP must be deployable through Docker Compose.

The target architecture should support logical components including:

- Frontend/web.
- Backend/API.
- Worker.
- PostgreSQL + pgvector.
- Redis.
- S3-compatible object storage / MinIO where needed.
- Reverse proxy where required.

---

## 8. Non-Functional Requirements

### NFR-001 Security

The platform must implement secure tenant isolation, RBAC, secret management, input validation, webhook verification where supported, rate-limit controls, safe file processing, and auditability.

### NFR-002 Privacy

Customer data must not be exposed across tenants.

Sensitive data must not be unnecessarily inserted into model prompts or application logs.

Retention/deletion policies should be configurable in later product phases; the data model should not prevent them.

### NFR-003 Reliability

Inbound webhooks should acknowledge valid events promptly and offload long-running work to workers.

Retries must be safe and idempotent.

Duplicate processing must not create duplicate customer responses or duplicate business-side effects.

### NFR-004 Maintainability

The backend must begin as a modular monolith with explicit boundaries.

Provider/channel/integration logic should remain behind adapters.

### NFR-005 Extensibility

Adding a new channel or AI provider should primarily require a new adapter and configuration rather than modification of unrelated core business logic.

### NFR-006 Observability

The application should expose structured logs, metrics, and trace-friendly execution identifiers.

Architecture should be compatible with OpenTelemetry-based instrumentation.

### NFR-007 Performance

Webhook handling must not wait for complete AI execution.

Customer-facing asynchronous AI responses should be optimized for practical conversational latency.

Exact service-level targets will be defined after MVP load assumptions are established.

### NFR-008 Testability

Default automated tests must not depend on paid external AI calls.

Provider and channel boundaries should be mockable/testable through contracts.

### NFR-009 Portability

Self-hosted deployment should not require proprietary cloud infrastructure.

Where possible use standard interfaces such as PostgreSQL, Redis, and S3-compatible object storage.

---

## 9. High-Level Data Concepts

The initial domain model is expected to include or evolve around the following concepts:

- Tenant.
- User.
- Tenant Membership.
- Role/Permission.
- Channel.
- Channel Account.
- Channel Capability/Configuration.
- Contact.
- External Identity.
- Conversation.
- Message.
- Message Attachment/Media.
- Agent.
- Prompt.
- Prompt Version.
- AI Provider.
- AI Model Registry Entry.
- AI Task Profile.
- AI Execution/Trace.
- Tool Definition.
- Tool Credential Binding.
- Tool Execution.
- Knowledge Base.
- Knowledge Source.
- Document/Chunk.
- Customer Memory.
- Policy.
- Human Assignment/Handoff.
- Usage Record.
- Audit Record.

This list defines concepts, not final table names. The database design must be completed in the architecture/domain-model phase.

---

## 10. AI Configuration Requirements

The administration panel should eventually expose a dedicated AI configuration area.

Conceptual structure:

### Providers

- OpenAI configuration.
- Gemini configuration.
- Future providers.

### Task Profiles

Examples:

- Customer Response.
- Voice Transcription.
- Intent Detection.
- Conversation Summary.
- Customer Memory Extraction.
- Vision/Image Understanding.
- Embeddings.
- Reranking.
- Complex Tool Reasoning.

### Per-task settings

- Provider.
- Model.
- Model parameters.
- Timeout.
- Retry.
- Fallback.
- Enabled/disabled.

The platform must avoid assumptions that one provider/model can perform all tasks.

---

## 11. Source-of-Truth Strategy

The agent should prefer information sources according to authority and freshness.

Conceptual priority:

1. Authorized live business APIs/tools for transactional data.
2. Structured business data sources.
3. Curated tenant knowledge/RAG.
4. Approved customer memory/context.
5. General model knowledge only when appropriate.

The product must not present stale RAG values as authoritative live transactional facts when a designated live source exists.

---

## 12. Human Handoff Requirements

Human handoff must be designed as part of the core conversation engine.

Potential escalation triggers include:

- Explicit customer request.
- Low confidence.
- Repeated failure.
- Complaint.
- Sensitive topic/action.
- Tool failure.
- High-risk write operation.
- Configured priority/VIP rule.

Human operators should eventually be able to:

- View queue/assigned conversations.
- Claim or receive assignments.
- Read full conversation history permitted by role.
- Send messages through the original channel.
- See AI suggestions in assist mode.
- Pause/resume AI autonomy.
- Resolve/close conversation.

---

## 13. Admin Panel Information Architecture (Initial)

The exact UX will be designed separately, but the product should anticipate primary sections such as:

- Dashboard.
- Conversations.
- Contacts.
- Agents.
- Channels.
- AI Models / Task Routing.
- Prompts.
- Knowledge Bases.
- Tools / Integrations.
- Policies.
- Users / Roles.
- Analytics.
- Usage / Costs.
- Audit Logs.
- Settings.

Platform-owner administration should remain distinct from tenant administration.

---

## 14. Commercial Product Requirements

To support use across different businesses, the product should be designed for configurable deployment rather than per-customer source-code forks.

Commercial requirements include:

- Tenant-specific branding/configuration path.
- Tenant-specific AI/provider settings.
- Tenant-specific credentials.
- Tenant-specific knowledge.
- Tenant-specific tools.
- Tenant-specific prompts/policies.
- Usage accounting per tenant.
- Capability limits/quotas foundation.
- Exportable/auditable operational data.
- Upgradeable Docker deployment.

Custom customer requirements should preferably be implemented through configuration, plugins/adapters, tools, or policies rather than dedicated forks.

---

## 15. Success Criteria for MVP

MVP is successful when a fresh deployment can demonstrate the following end-to-end flow without code changes for the tenant:

1. Deploy system through documented Docker Compose procedure.
2. Create/configure a tenant.
3. Create tenant users and roles.
4. Configure OpenAI and/or Gemini provider credentials.
5. Configure model routing for at least customer response and voice transcription tasks.
6. Connect Telegram.
7. Enable text and voice inbound capabilities.
8. Configure website chat.
9. Upload PDF/Excel knowledge.
10. Configure at least one live external business tool/API.
11. Configure an AI agent and prompt.
12. Receive a text customer message.
13. Receive a voice customer message and transcribe it using the configured task model.
14. Use RAG when appropriate.
15. Use a live tool when appropriate.
16. Send a response through the originating channel.
17. Store contact, identity, conversation, and message history.
18. Escalate a conversation to a human.
19. Record relevant AI/tool/audit traces.
20. Display basic operational and AI usage metrics.

---

## 16. MVP Acceptance Gates

The MVP must not be considered production-ready until the following gates are satisfied:

### Product gate

- Core journeys work end-to-end.
- Tenant can configure the system without source-code edits.

### Architecture gate

- Channel core is provider-independent.
- AI core is provider-independent.
- Tenant isolation is enforced.
- Long-running message processing uses worker/queue architecture.

### Security gate

- No real credentials are committed.
- Tool credentials are not exposed to models or browser clients.
- Cross-tenant access tests pass.
- Webhook validation is implemented where supported.
- File-processing risks are mitigated.

### Quality gate

- Automated tests cover critical domain and security paths.
- Database migrations are reproducible.
- Docker deployment is reproducible.
- Errors are observable and diagnosable.

### Operations gate

- Backup/restore procedure is defined for persistent services.
- Upgrade/migration procedure is defined.
- Required environment variables are documented.
- Health checks exist for critical runtime components where appropriate.

---

## 17. Key Product Risks

### 17.1 Over-expanding channel scope

Mitigation: stabilize Telegram + website first and maintain connector contracts for future channels.

### 17.2 Provider lock-in

Mitigation: canonical AI task/provider abstraction.

### 17.3 AI hallucination on live business data

Mitigation: explicit source-of-truth strategy and authoritative live tools.

### 17.4 Tool misuse

Mitigation: application-side authorization, risk classes, human approval, and audit.

### 17.5 Cross-tenant data leakage

Mitigation: server-side tenant isolation, negative security tests, and tenant-aware retrieval/storage.

### 17.6 Cost growth

Mitigation: task-based model routing, usage accounting, model fallback/strategy controls, and tenant quotas in later phases.

### 17.7 Unmaintainable AI-generated code

Mitigation: `AGENTS.md`, ADRs, atomic tasks, code review, automated tests, and specification-first implementation.

### 17.8 Treating n8n as the application core

Mitigation: keep n8n as an external automation/integration layer while canonical state remains in the platform.

---

## 18. Product Decisions Requiring Later ADR/Specification

The following require explicit follow-up design before implementation:

- Final module boundaries and repository structure.
- Authentication mechanism and token/session strategy.
- Tenant isolation implementation details.
- Job queue library choice.
- Exact database schema.
- Encryption/secret storage implementation.
- AI provider adapter contract.
- Canonical message schema.
- Tool contract and authorization model.
- RAG ingestion/chunking/retrieval design.
- Customer memory lifecycle.
- Human handoff state machine.
- Website chat authentication/session model.
- Usage/cost accounting design.
- Production reverse proxy/TLS model.
- Backup/restore and upgrade strategy.

---

## 19. Initial Delivery Strategy

Development should proceed through controlled milestones rather than implementing all modules concurrently.

Recommended sequencing concept:

1. Product governance and architecture documentation.
2. Repository/Docker skeleton.
3. Core configuration and infrastructure.
4. Tenant/auth/RBAC foundation.
5. Conversation/contact domain.
6. AI provider gateway and task routing.
7. Telegram + website channel foundation.
8. Agent/prompt runtime.
9. Tools.
10. Knowledge/RAG.
11. Voice/transcription.
12. Human handoff.
13. Customer memory/intelligence.
14. Audit/usage/analytics.
15. Hardening, deployment, and commercial MVP validation.

Exact milestone breakdown belongs in `ROADMAP.md`.

---

## 20. Definition of Product Completion

A feature is complete only when its user-visible behavior, backend enforcement, security, tests, observability, and deployment implications have been addressed according to `AGENTS.md` and applicable ADRs.

Code generation alone does not constitute feature completion.

---

## 21. Document Governance

This PRD describes product intent and scope.

Priority of project guidance:

1. Explicit approved product-owner decision.
2. Security and tenant-isolation invariants.
3. Accepted ADRs for architectural implementation decisions.
4. This PRD for product scope/requirements.
5. `AGENTS.md` for engineering and agent execution rules.
6. Feature/task specifications for implementation details.

Material changes to MVP scope should update this document and the roadmap before implementation proceeds.
