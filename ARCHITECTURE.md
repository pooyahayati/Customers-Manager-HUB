# System Architecture

## Customers Manager HUB

**Status:** Draft v0.1  
**Architecture style:** Modular monolith + independent workers  
**Deployment target:** Docker Compose initially, with a path to SaaS and future service extraction

---

## 1. Architecture Goals

The architecture must support a commercial, multi-tenant, omnichannel AI customer interaction platform without coupling business logic to a specific communication channel, AI provider, workflow engine, or knowledge store.

Primary goals:

- Multi-tenant isolation by design.
- Channel-independent conversation core.
- Provider-independent AI runtime.
- Task-based model routing.
- Secure tool execution.
- Provider-independent RAG.
- Explicit human handoff.
- Auditable and observable AI execution.
- Docker-first deployment.
- Modular boundaries that can evolve into services if scale requires it.
- Avoid premature microservices and unnecessary infrastructure.

---

## 2. High-Level System Context

```text
                         ┌────────────────────────┐
                         │      Admin Panel       │
                         │   Next.js / Web App    │
                         └───────────┬────────────┘
                                     │ HTTPS/API
                                     ▼
┌──────────────┐          ┌────────────────────────┐
│ Telegram     │─────────▶│                        │
├──────────────┤          │      Backend API       │
│ Website Chat │─────────▶│      FastAPI App       │
├──────────────┤          │                        │
│ WhatsApp*    │─────────▶│  Auth / Tenant / API   │
├──────────────┤          │  Channel Webhooks      │
│ Instagram*   │─────────▶│  Admin APIs            │
└──────────────┘          └───────────┬────────────┘
                                     │
                           Persist + enqueue
                                     │
                        ┌────────────▼────────────┐
                        │         Redis           │
                        │ Queue / Cache / Locks   │
                        └────────────┬────────────┘
                                     │
                                     ▼
                        ┌─────────────────────────┐
                        │         Worker          │
                        │ Conversation Processing │
                        │ AI / RAG / Tools / STT  │
                        └─────┬────────┬──────────┘
                              │        │
                     ┌────────▼───┐ ┌──▼───────────────┐
                     │ PostgreSQL │ │ Object Storage   │
                     │ + pgvector │ │ S3 / MinIO       │
                     └────────────┘ └──────────────────┘
                              │
          ┌───────────────────┼────────────────────────┐
          │                   │                        │
          ▼                   ▼                        ▼
 ┌────────────────┐  ┌─────────────────┐     ┌─────────────────┐
 │  AI Providers  │  │ Business Tools  │     │ External Flows  │
 │ OpenAI/Gemini  │  │ API/Sheets/CRM  │     │ n8n/Webhooks    │
 └────────────────┘  └─────────────────┘     └─────────────────┘
```

`*` Deferred native connectors in MVP, but supported by the connector architecture.

---

## 3. Architectural Style

### 3.1 Modular Monolith

The backend begins as one deployable application codebase with explicit internal modules.

This is deliberate.

We do not begin with microservices because the initial product needs rapid iteration, strong transactional consistency, simple deployment, and manageable operational complexity.

Module boundaries must still be designed as if future extraction is possible.

Core rule:

> Modules may collaborate through defined application interfaces, domain contracts, and events. They must not casually reach into another module's persistence internals.

### 3.2 Independent Workers

The API process and worker process may use the same codebase/image but run as separate container processes.

The API handles:

- Admin/API requests.
- Authentication.
- Channel webhooks.
- Fast validation.
- Persistence of inbound events.
- Enqueueing.

Workers handle:

- AI execution.
- Tool calls.
- Voice transcription.
- RAG ingestion/retrieval workflows.
- Long external API operations.
- Outbound channel dispatch.
- Scheduled/background processing.

---

## 4. Logical Layers

The backend should follow four conceptual layers where practical.

### 4.1 Interface Layer

Responsibilities:

- REST APIs.
- Webhooks.
- Admin-facing endpoints.
- Website chat transport endpoints.
- Request/response schema validation.

Must not own domain rules.

### 4.2 Application Layer

Responsibilities:

- Use-case orchestration.
- Commands/queries.
- Transaction boundaries.
- Permission checks.
- Coordination between domain modules.
- Event publishing.

### 4.3 Domain Layer

Responsibilities:

- Core business entities.
- State transitions.
- Domain invariants.
- Business rules independent from FastAPI, Redis, AI provider SDKs, or databases where practical.

### 4.4 Infrastructure Layer

Responsibilities:

- SQLAlchemy repositories.
- Redis queue/cache.
- AI provider adapters.
- Telegram/other channel SDK/API adapters.
- S3/MinIO storage.
- Tool HTTP clients.
- External integrations.

---

## 5. Proposed Backend Modules

The exact package structure will be finalized during repository bootstrap, but the domain must preserve the following boundaries.

### 5.1 Identity & Access

Owns:

- Platform users.
- Authentication.
- Tenant membership.
- Roles.
- Permissions.
- Session/token policies.

Does not own:

- Customer contacts.
- Channel identities.

### 5.2 Tenants

Owns:

- Tenant/workspace lifecycle.
- Business configuration.
- Tenant status.
- Tenant-level feature configuration.

All tenant-owned operations depend on an authenticated tenant context.

### 5.3 Channels

Owns:

- Channel definitions.
- Channel account configuration.
- Connector capabilities.
- Credential references.
- Webhook configuration state.
- Connector enable/disable settings.
- Allowed inbound message types.

Provides channel adapters.

Does not own conversation intelligence or AI reasoning.

### 5.4 Contacts

Owns:

- Contact/customer records.
- External identities.
- Identity resolution.
- Contact merge policy.
- Basic profile/contact fields.

### 5.5 Conversations

Owns:

- Conversation lifecycle.
- Message records.
- Attachments/media references.
- Conversation state.
- Assignment/handoff state references.

Acts as the canonical interaction history.

### 5.6 Agents

Owns:

- Agent definitions.
- Agent status.
- Tool permissions.
- Knowledge permissions.
- Channel applicability.
- Handoff configuration.

### 5.7 Prompts

Owns:

- Prompt definitions.
- Prompt versions.
- Draft/published/archive lifecycle.
- Layered prompt components.

Historical execution must reference the effective prompt version.

### 5.8 AI Gateway

Owns:

- Provider registry.
- Model registry.
- Task profiles.
- Provider adapters.
- Routing/fallback logic.
- Canonical generation/transcription/embedding/etc contracts.
- Usage normalization.

Does not own customer/business decisions.

### 5.9 Tools

Owns:

- Tool definitions.
- Input/output schemas.
- Credential bindings.
- Risk classification.
- Agent authorization.
- Execution policy.
- Tool invocation trace.

### 5.10 Knowledge

Owns:

- Knowledge bases.
- Knowledge sources.
- Document ingestion state.
- Text/table extraction outputs.
- Chunk metadata.
- Embeddings/vector records.
- Retrieval.
- Source provenance.

### 5.11 Customer Memory

Owns:

- Structured memory items.
- Fact/inference classification.
- Confidence.
- Provenance.
- Freshness/expiration.
- Approved personalization context.

Does not replace raw conversation history.

### 5.12 Policies

Owns deterministic runtime rules such as:

- Tool allow/deny.
- Approval requirements.
- Handoff triggers.
- Message type policies.
- Working-hours logic.
- Autonomy constraints.

Policy is enforcement logic, not prompt prose.

### 5.13 Human Operations

Owns:

- Human assignments.
- Queue status.
- Claim/release operations.
- Human-active state.
- AI assist-only mode.
- Resolution/closure workflow.

### 5.14 Audit

Owns immutable or append-oriented records of privileged operations and relevant configuration changes.

### 5.15 Usage & Analytics

Owns:

- AI usage records.
- Token usage.
- Provider/model costs where available.
- Conversation metrics.
- Tool success/failure metrics.
- Operational KPI aggregation.

---

## 6. Canonical Message Flow

All channels must converge on one canonical processing flow.

```text
External Channel
      │
      ▼
Connector Webhook
      │
      ├─ Authenticate / verify source
      ├─ Validate payload
      ├─ Check channel enabled
      ├─ Check message capability policy
      ▼
Normalize External Payload
      │
      ▼
Canonical Inbound Message
      │
      ├─ Resolve tenant
      ├─ Resolve/create external identity
      ├─ Resolve/create contact
      ├─ Resolve/create conversation
      ├─ Deduplicate by external message/event ID
      ▼
Persist Message
      │
      ▼
Enqueue Processing Job
      │
      ▼
Return webhook acknowledgement

Worker:
      │
      ▼
Load Conversation Context
      │
      ▼
Evaluate Conversation/Handoff State
      │
      ├─ Human active? → no autonomous AI response
      ▼
Select Agent
      │
      ▼
Policy Pre-check
      │
      ▼
Build Context
      ├─ Prompt stack
      ├─ Customer context
      ├─ Conversation summary/history
      ├─ Knowledge retrieval
      └─ Tool definitions
      │
      ▼
Resolve AI Task Profile
      │
      ▼
AI Provider Adapter
      │
      ├─ Structured response
      ├─ Tool request(s)
      └─ Final answer
      │
      ▼
Tool Runtime / RAG loop if needed
      │
      ▼
Policy Post-check / Output Validation
      │
      ▼
Persist AI Trace + Outbound Message
      │
      ▼
Channel Adapter
      │
      ▼
External Customer
```

---

## 7. Canonical Message Contract

Channel-specific payloads must be normalized into a common internal contract.

Conceptual shape:

```json
{
  "tenant_id": "...",
  "channel": "telegram",
  "channel_account_id": "...",
  "external_event_id": "...",
  "external_message_id": "...",
  "external_conversation_id": "...",
  "sender_identity": {
    "external_id": "...",
    "display_name": "..."
  },
  "message": {
    "type": "text|voice|image|document|...",
    "text": "...",
    "attachments": []
  },
  "received_at": "...",
  "metadata": {}
}
```

This is a conceptual contract, not yet the final Pydantic schema.

Required characteristics:

- Stable internal message type enum.
- External IDs retained for idempotency.
- Original channel metadata retained safely for debugging.
- Attachment/media references separated from binary storage.
- Tenant context explicit internally.

---

## 8. Channel Adapter Architecture

Each channel implements a canonical connector interface.

Conceptual interface:

```text
ChannelConnector

capabilities()
verify_webhook(...)
parse_inbound(...)
normalize_inbound(...)
send_text(...)
send_media(...)
fetch_media(...)
health_check(...)
```

Not every connector must implement every capability.

The connector declares supported capabilities.

Example capability registry:

```text
telegram:
  text.receive = true
  text.send = true
  voice.receive = true
  voice.send = true
  image.receive = true
  document.receive = true
  location.receive = true
```

Tenant configuration can independently allow or deny supported inbound types.

The runtime therefore distinguishes:

- Connector supports capability.
- Tenant enables capability.
- Conversation/agent policy permits processing.

---

## 9. AI Gateway Architecture

The core system must never invoke provider SDKs from business modules directly.

Conceptual structure:

```text
                 AI Gateway
                     │
             ┌───────┴────────┐
             │ Task Resolver  │
             └───────┬────────┘
                     │
          ┌──────────┴───────────┐
          │ Provider Abstraction │
          └───────┬───────┬──────┘
                  │       │
          ┌───────▼──┐ ┌──▼────────┐
          │ OpenAI   │ │ Gemini    │
          │ Adapter  │ │ Adapter   │
          └──────────┘ └───────────┘
```

### 9.1 AI Task Types

Initial task taxonomy should anticipate:

- `customer_response`
- `voice_transcription`
- `intent_classification`
- `conversation_summary`
- `customer_memory_extraction`
- `vision_understanding`
- `embedding`
- `reranking`
- `complex_reasoning`

Names may change before implementation, but behavior must remain task-oriented.

### 9.2 Task Profile

Conceptual configuration:

```json
{
  "task_type": "customer_response",
  "provider": "openai",
  "model_id": "configured-model-id",
  "parameters": {},
  "timeout_seconds": 30,
  "retry_policy": {},
  "fallback_chain": [
    {
      "provider": "google",
      "model_id": "configured-fallback-model"
    }
  ]
}
```

Exact model IDs must come from configuration/registry, not from scattered application constants.

### 9.3 Canonical AI Operations

The gateway should expose provider-neutral operations such as:

- Generate response.
- Generate structured output.
- Execute tool-capable generation.
- Transcribe audio.
- Generate embeddings.
- Analyze image/media.

Provider-specific capabilities may vary; unsupported operations must fail clearly.

---

## 10. Prompt Composition Architecture

The runtime must compose a controlled prompt/context stack.

Conceptual order:

```text
Platform Policy
      ↓
Tenant Policy
      ↓
Agent Instructions
      ↓
Channel Instructions
      ↓
Conversation State / Runtime Rules
      ↓
Customer Memory / Context
      ↓
Retrieved Knowledge
      ↓
Tool Context / Results
      ↓
Current Customer Message
```

Trusted layers must not be overwritten by user-provided content.

Prompt composition and prompt storage are separate concerns.

Published prompt versions must be immutable historically or otherwise reproducibly traceable.

---

## 11. Tool Runtime Architecture

Tools are business capabilities exposed to AI through a controlled execution layer.

The model can request a tool call.

The model cannot authorize the tool call.

Conceptual flow:

```text
AI tool request
      │
      ▼
Schema validation
      │
      ▼
Tenant validation
      │
      ▼
Agent permission check
      │
      ▼
Policy/risk evaluation
      │
      ├─ approval required → Human Approval
      │
      ▼
Credential resolution
      │
      ▼
Tool execution
      │
      ▼
Output schema validation
      │
      ▼
Audit / trace
      │
      ▼
Safe structured result to AI runtime
```

### 11.1 Tool Contract

A tool should support metadata such as:

```text
id
name
version
description
input_schema
output_schema
operation_type: read | write
risk_level
credential_binding
allowed_agents
timeout
retry_policy
requires_confirmation
rate_limit
```

### 11.2 Credentials

Credential material must never be sent to the AI model.

Models see logical tool descriptions only.

Credential resolution happens inside trusted server infrastructure immediately before execution.

---

## 12. Knowledge and RAG Architecture

### 12.1 Ingestion Pipeline

```text
Knowledge Source
      │
      ▼
Upload / Fetch
      │
      ▼
Object Storage
      │
      ▼
Parser
      │
      ▼
Text/Table Extraction
      │
      ▼
Cleaning / Normalization
      │
      ▼
Chunking
      │
      ▼
Metadata + Provenance
      │
      ▼
Embedding Task
      │
      ▼
PostgreSQL + pgvector
```

### 12.2 Retrieval Pipeline

```text
Customer Query
      │
      ▼
Retrieval Query Construction
      │
      ▼
Tenant-scoped Vector Search
      │
      ▼
Metadata Filtering
      │
      ▼
Optional Reranking
      │
      ▼
Top Evidence Chunks
      │
      ▼
Prompt Context
```

### 12.3 Source-of-Truth Rule

RAG is not the source of truth for volatile transactional facts when a live business API exists.

Use live tools for:

- Price.
- Inventory.
- Order state.
- Shipment state.
- Account/customer balances.

Use knowledge retrieval for:

- Product explanation.
- Manuals.
- Policies.
- Articles.
- FAQ.
- Warranty/usage guidance.

---

## 13. Voice Processing Architecture

Voice messages enter through the normal channel flow.

```text
Voice Message
      │
      ▼
Channel Adapter
      │
      ▼
Store Media Reference
      │
      ▼
Voice Processing Job
      │
      ▼
Resolve `voice_transcription` AI Task Profile
      │
      ▼
Configured AI Provider/Model
      │
      ▼
Validated Transcript
      │
      ▼
Persist Transcript + Trace
      │
      ▼
Continue Normal Conversation Pipeline
```

The transcription provider may be different from the customer-response provider.

This is a required architecture invariant.

---

## 14. Customer Identity Architecture

One real customer may appear through multiple channels.

Conceptual model:

```text
Contact
 ├── ExternalIdentity: telegram / 12345
 ├── ExternalIdentity: whatsapp / +...
 ├── ExternalIdentity: instagram / ...
 ├── ExternalIdentity: website / ...
 └── ExternalIdentity: crm / customer_...
```

Identity resolution should support:

- Exact external identity match.
- Verified phone/email association.
- Manual merge.
- Configured trusted source linkage.

Weak AI inference must not silently merge customer records.

---

## 15. Customer Memory Architecture

Memory is distinct from raw message history.

Three conceptual context layers:

```text
Conversation Memory
Customer Memory
Business Knowledge
```

### Conversation Memory

Short-lived context for the current interaction/thread.

### Customer Memory

Longer-lived structured operational context about the contact.

### Business Knowledge

Tenant-owned shared information.

Customer memory items should support fields such as:

```text
contact_id
type
key/value or structured payload
fact_or_inference
source
confidence
created_at
updated_at
expires_at
```

Sensitive or unnecessary inferred traits should not be generated merely for personalization.

---

## 16. Human Handoff State Architecture

Conversation control must be explicit.

Conceptual state model:

```text
AI_ACTIVE
    │
    ├── escalation ─────▶ HUMAN_REQUIRED
    │                         │
    │                         ▼
    │                  WAITING_FOR_AGENT
    │                         │
    │                         ▼
    │                    HUMAN_ACTIVE
    │                         │
    │          ┌──────────────┼───────────────┐
    │          ▼              ▼               ▼
    │  WAITING_CUSTOMER    RESOLVED       RETURN_TO_AI
    │                                          │
    └──────────────────────────────────────────┘
```

Final enum/state machine names will be decided during domain modeling.

Critical invariant:

> When human takeover is active, autonomous AI outbound messaging is disabled unless the conversation is explicitly configured for AI assist-only suggestions.

---

## 17. Policy Architecture

Policies are deterministic rules executed by the application.

Examples:

```text
IF tool.risk == critical
THEN require human approval
```

```text
IF conversation.mode == HUMAN_ACTIVE
THEN deny autonomous outbound AI message
```

```text
IF inbound.type == image
AND channel_config.allow_image == false
THEN reject/ignore according to configured channel behavior
```

```text
IF outside_business_hours
THEN route to configured after-hours behavior
```

Policy must not rely solely on a natural-language prompt instruction.

---

## 18. Event and Queue Architecture

Redis is the initial queue/cache/coordination technology.

The exact queue library will be selected later through an ADR or implementation specification.

Potential event/job categories:

- Process inbound message.
- Transcribe voice.
- Ingest knowledge source.
- Generate embeddings.
- Dispatch outbound message.
- Extract customer memory.
- Generate conversation summary.
- Retry external tool operation.
- Refresh integration state.

### 18.1 Idempotency

All externally-triggered jobs must tolerate duplicate delivery.

At minimum, inbound channel events require uniqueness/idempotency based on tenant + channel account + external event/message identifier where available.

Outbound retries must not send duplicate customer messages.

Mutating tool retries must not duplicate business-side effects.

---

## 19. Database Architecture

Initial database:

```text
PostgreSQL + pgvector
```

Why:

- Transactional data.
- Relational domain model.
- Strong constraints.
- Mature operational ecosystem.
- Vector retrieval without an additional database in MVP.

### 19.1 Tenant Isolation

Tenant-owned tables must carry explicit tenant ownership unless they are safely linked through an invariant that guarantees tenant scope.

Application queries must enforce tenant scope.

Potential future defense-in-depth mechanisms may include PostgreSQL Row-Level Security, but adoption should be an explicit ADR rather than assumed automatically.

### 19.2 Schema Migrations

All schema changes require migrations.

Recommended migration technology for the proposed Python/SQLAlchemy stack: Alembic, subject to implementation ADR/spec confirmation.

---

## 20. Object Storage Architecture

Binary files should not live directly inside PostgreSQL unless a specific small-object use case justifies it.

Use S3-compatible object storage for:

- Uploaded PDFs.
- Excel sources.
- Voice/audio media.
- Images/documents.
- Future export/archive artifacts.

Self-hosted default:

```text
MinIO
```

Cloud deployments may use another S3-compatible provider without changing domain behavior.

Database stores metadata and object references, not raw large binaries.

---

## 21. Cache Strategy

Redis may cache:

- Non-sensitive short-lived configuration.
- Model/provider registry lookups.
- Channel runtime configuration.
- Rate-limit counters.
- Distributed locks.
- Queue state.

Redis is not the authoritative source for core domain state.

Cache loss must not destroy canonical business data.

---

## 22. n8n Integration Boundary

n8n remains optional and external to the core product runtime.

Supported architecture:

```text
Customers Manager HUB
      │
      ├── Webhook/Event → n8n
      │
      └── Tool/API ← n8n or external workflow
```

Good n8n use cases:

- CRM synchronization.
- Notifications.
- Lead workflow automation.
- Post-resolution workflows.
- External business automations.

Forbidden core dependency:

```text
Conversation state → n8n as source of truth
Tenant authorization → n8n
Primary AI runtime → mandatory n8n workflow
```

The core product must continue operating without n8n.

---

## 23. API Architecture

Initial application API style:

```text
REST + JSON
```

API versioning should begin with a stable prefix such as:

```text
/api/v1/...
```

Final routing conventions will be documented before implementation.

API requirements:

- Pydantic request/response validation.
- Consistent error model.
- Server-side authorization.
- Server-side tenant context.
- Pagination for list endpoints.
- Correlation/request IDs.
- OpenAPI generation via FastAPI.

Do not authorize access simply because the client sends a tenant ID.

---

## 24. Website Chat Architecture

The website chat connector should behave like another channel, not a special bypass into the agent runtime.

Conceptual flow:

```text
Embedded Web Widget
      │
      ▼
Website Channel API
      │
      ▼
Website External Identity / Session
      │
      ▼
Canonical Message
      │
      ▼
Conversation Engine
```

The exact session/authentication approach for anonymous and known website visitors requires a dedicated specification.

The widget must never expose privileged tenant secrets or AI provider credentials.

---

## 25. Security Architecture

Security boundaries:

### 25.1 Tenant Boundary

Every tenant-owned request must resolve a trusted tenant context and enforce it server-side.

### 25.2 AI Boundary

AI output is untrusted data.

AI cannot grant itself permissions.

### 25.3 Tool Boundary

Tool execution requires independent application authorization and policy evaluation.

### 25.4 External Webhook Boundary

Connector webhooks must verify authenticity/signatures/secrets when the external platform supports them.

### 25.5 File Boundary

Uploaded files are untrusted input.

File processing must protect against:

- MIME/extension mismatch.
- Oversized files.
- Malicious archives.
- Path traversal.
- Unsafe parser execution.

### 25.6 URL Fetching Boundary

Server-side URL retrieval must protect against SSRF and private-network access unless explicitly required and permitted.

### 25.7 Secret Boundary

Secrets must remain server-side and must not appear in:

- Git repository.
- Browser bundles.
- Prompt context.
- Normal application logs.
- AI trace payloads.

---

## 26. Audit Architecture

Audit records are distinct from debug logs.

Audit should capture important operations such as:

- User/role changes.
- Provider configuration changes.
- Channel configuration changes.
- Prompt publication.
- Tool definition/credential changes.
- High-impact tool execution.
- Human approval.
- Contact merge.
- Policy changes.

A conceptual audit record includes:

```text
tenant_id
actor_type
actor_id
action
resource_type
resource_id
timestamp
safe_metadata
request/correlation_id
```

Secrets and unnecessary raw customer data must not be copied into audit payloads.

---

## 27. AI Trace Architecture

An AI execution trace should associate:

```text
trace_id
tenant_id
conversation_id
message_id
agent_id
prompt_version_id
task_type
provider
model_id
retrieval references
tool calls
latency
usage/tokens
cost information
fallback attempts
error state
```

Tracing must support debugging questions such as:

> Why did the AI send this answer?

Without requiring uncontrolled storage of the provider's hidden reasoning.

The system records inputs, outputs, sources, tool activity, configuration, and observable execution metadata—not private chain-of-thought.

---

## 28. Observability Architecture

The system should be compatible with OpenTelemetry-style instrumentation.

Three observability categories:

### Logs

Structured application/runtime logs.

### Metrics

Examples:

- Request latency.
- Queue depth.
- Worker failures.
- AI latency.
- Tool failures.
- Channel delivery failure rate.

### Traces

Correlation across:

```text
Webhook → Queue → Worker → AI → Tool → Channel response
```

All three must use consistent correlation IDs where practical.

---

## 29. Docker Deployment Architecture

Initial Compose topology:

```text
┌─────────────────────────────────────────────┐
│               Reverse Proxy                 │
│             Traefik or Nginx                │
└───────────────┬─────────────────────────────┘
                │
       ┌────────┴─────────┐
       │                  │
       ▼                  ▼
┌─────────────┐     ┌─────────────┐
│     web     │     │     api     │
│   Next.js   │     │   FastAPI   │
└─────────────┘     └──────┬──────┘
                           │
             ┌─────────────┼──────────────┐
             │             │              │
             ▼             ▼              ▼
       ┌──────────┐  ┌──────────┐  ┌────────────┐
       │ postgres │  │  redis   │  │   minio    │
       │ pgvector │  │          │  │ objectstore│
       └──────────┘  └────┬─────┘  └────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   worker    │
                    │ same app    │
                    │ code/image  │
                    └─────────────┘
```

Optional later components:

- Scheduler.
- Dedicated observability stack.
- Reverse proxy if supplied externally.
- Dedicated ingestion workers.

---

## 30. Initial Technology Direction

Subject to ADR confirmation during implementation:

### Backend

- Python.
- FastAPI.
- Pydantic.
- SQLAlchemy.
- Alembic.

### Frontend

- Next.js.
- TypeScript.

### Database

- PostgreSQL.
- pgvector.

### Queue/cache

- Redis.

### Object storage

- S3-compatible interface.
- MinIO for default self-hosted Compose deployment.

### AI

- Internal provider abstraction.
- OpenAI adapter.
- Gemini adapter.

### Deployment

- Docker.
- Docker Compose.

### Observability

- OpenTelemetry-compatible architecture.

Exact versions and package choices must be made using current supported releases during implementation.

---

## 31. Proposed Repository Structure

This is the target concept, not yet an instruction to create all files immediately.

```text
Customers-Manager-HUB/
│
├── AGENTS.md
├── PRD.md
├── ARCHITECTURE.md
├── ROADMAP.md
├── README.md
├── .env.example
├── docker-compose.yml
│
├── apps/
│   ├── api/
│   └── web/
│
├── packages/
│   └── ... shared packages if justified
│
├── infra/
│   ├── docker/
│   └── ...
│
├── docs/
│   ├── adr/
│   ├── api/
│   ├── security/
│   └── deployment/
│
├── specs/
│   ├── product/
│   ├── modules/
│   └── tasks/
│
└── tests/
```

A final Python package/module tree will be designed before the first backend feature implementation.

---

## 32. Dependency Rules

Business/domain modules must not import provider SDKs directly.

Examples of forbidden dependencies:

```text
contacts → openai SDK
conversations → telegram SDK
agents → Gemini SDK
policies → n8n runtime
```

Preferred dependency direction:

```text
Domain/Application
       │
       ▼
Internal Interfaces
       │
       ▼
Infrastructure Adapters
       │
       ▼
External Providers/Channels
```

---

## 33. Transaction Boundaries

Operations that modify multiple related domain records should be committed atomically where consistency requires it.

Examples:

- Creating external identity + contact association.
- Persisting inbound message + processing event/outbox record.
- Tool execution state + audit state where required.
- Human assignment + conversation state transition.

Long external network calls should generally not hold open database transactions.

---

## 34. Reliable Event Publication

A future implementation should evaluate the transactional outbox pattern for events that must be reliably emitted after database state changes.

Example:

```text
DB transaction:
  save message
  save outbox event
COMMIT

worker/publisher:
  publish outbox event to queue
  mark delivered
```

This avoids a failure window where database persistence succeeds but queue publication fails.

Whether this is required in the first bootstrap release should be decided in a dedicated implementation specification/ADR.

---

## 35. Failure Handling Strategy

Failures must be categorized rather than handled with generic retry loops.

Categories may include:

- Validation failure.
- Authorization failure.
- Policy denial.
- Provider timeout.
- Provider rate limit.
- Provider invalid response.
- Tool timeout.
- Tool business error.
- Channel delivery failure.
- File processing failure.
- Queue infrastructure failure.

Retries should apply only where retrying is safe and useful.

Fallback AI models may be attempted when configured and when the failure category permits it.

---

## 36. Cost Architecture

AI usage must become tenant-attributable.

Each execution record should capture available information needed for cost calculation, such as:

- Provider.
- Model.
- Input usage.
- Output usage.
- Additional provider-specific billable units.
- Calculated/returned cost when available.

Pricing tables change over time; provider pricing must not be embedded as permanent hard-coded business logic without a maintainable update mechanism.

---

## 37. Scalability Path

The architecture should scale incrementally.

### Stage 1

Single-host Docker Compose:

- 1 web.
- 1 API.
- 1 worker.
- PostgreSQL.
- Redis.
- MinIO.

### Stage 2

Scale stateless processes horizontally:

- Multiple API instances.
- Multiple workers.
- Managed or dedicated database/cache/object storage.

### Stage 3

Only if actual operational data justifies it, extract high-load or independently-scaled modules, such as:

- Media/document ingestion.
- Channel dispatch.
- Analytics processing.
- AI execution workers.

Microservice extraction must follow demonstrated scaling/ownership needs, not anticipation alone.

---

## 38. Architecture Invariants

The following rules are treated as invariants unless superseded by an accepted ADR:

1. Tenant isolation is server-side.
2. Core conversations are channel-independent.
3. AI business logic is provider-independent.
4. AI models are selected through task profiles.
5. Tool calls require application authorization.
6. Credentials never enter AI prompts.
7. RAG is not authoritative for volatile data when a live source exists.
8. Human takeover disables autonomous AI responses by default.
9. Webhooks do not run full AI workflows synchronously.
10. Core state is stored in platform-owned persistence, not n8n.
11. AI execution is traceable without storing private chain-of-thought.
12. The initial architecture remains deployable through Docker Compose.

---

## 39. Required Follow-up ADRs

The following architecture decisions should be captured as ADRs before or during implementation:

- ADR-001: Modular monolith + worker architecture.
- ADR-002: Provider-independent AI Gateway and task routing.
- ADR-003: Channel adapter/canonical message architecture.
- ADR-004: Multi-tenant isolation strategy.
- ADR-005: Event-driven inbound message processing.
- ADR-006: PostgreSQL + pgvector as initial persistence/vector stack.
- ADR-007: Redis queue technology/library.
- ADR-008: Object storage strategy.
- ADR-009: Authentication/session strategy.
- ADR-010: Tool contract and credential security.
- ADR-011: Human handoff state machine.
- ADR-012: Transactional outbox decision.

These documents should be concise decisions with context, alternatives, and consequences—not duplicate copies of this architecture document.

---

## 40. Architecture Review Checklist

Before approving a meaningful feature, review:

- Does it preserve tenant isolation?
- Is channel-specific behavior kept inside the connector?
- Is provider-specific AI behavior kept inside the adapter?
- Is the task model configurable?
- Is tool authorization server-side?
- Are secrets protected?
- Is long-running work asynchronous?
- Is processing idempotent where external events are involved?
- Are domain and infrastructure concerns separated?
- Is the operation observable and auditable where needed?
- Does it create an unnecessary new service/database/framework?
- Can it still be deployed cleanly through Docker?

---

## 41. Document Governance

This file describes the current intended technical architecture.

`PRD.md` defines product requirements and scope.

`AGENTS.md` defines engineering and coding-agent rules.

ADRs define approved specific technical decisions when alternatives exist.

If implementation reveals that an architecture rule must change, update the appropriate ADR/document deliberately rather than silently diverging in code.
