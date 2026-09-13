# Customers Manager HUB — Business Operating Model V1

Status: Approved product direction; implementation pending
Updated: 2026-09-10
Audience: Product, architecture, implementation, QA

## 1. Goal

Define a coherent, low-friction operating model for ordinary Business users while preserving the platform's multi-tenant, provider-independent, auditable architecture.

The primary user journey is:

1. Describe the Business.
2. Connect trusted sources.
3. Prepare and validate knowledge.
4. Configure an Agent.
5. Define enforceable rules.
6. Test the Agent.
7. Connect a channel.
8. Operate conversations and improve missing information.

## 2. Product principles

- Business users work with goals and plain-language choices; raw prompts and technical identifiers live under advanced controls.
- AI may propose drafts, classifications, and improvements, but never silently publishes configuration or changes trusted facts.
- Stable knowledge and volatile live data are separate concepts.
- Credentials are never exposed to the browser, prompts, model providers, or logs.
- Tenant isolation and authorization are enforced by the API, never by frontend filtering alone.
- Empty pages must explain why no data exists and present the next valid action.
- Advanced capability remains available through progressive disclosure instead of cluttering the default workflow.

## 3. Roles and surfaces

### 3.1 Platform Owner

The Owner surface contains only platform-level capabilities:

- Business administration.
- Business-user administration.
- AI provider credentials and connection health.
- Provider/model registry.
- Task-based model routing.
- Model pricing and Rial-based billing configuration.
- Business wallet adjustments and platform usage oversight.
- Configuration Assistant model, prompt versions, guardrails, usage limits, and audit.
- Platform audit and operational health.

The Owner must not see Business operational navigation such as contacts, conversations, inbox, agents, channels, or knowledge unless entering an explicitly privileged support context with audit.

### 3.2 Business roles

- Admin: manages Business configuration, connections, agents, knowledge, rules, users, and channels.
- Supervisor: monitors operations, manages handoff and assignments, reviews analytics, and may edit operational configuration when granted.
- Agent: works assigned conversations and contacts; cannot access credentials or platform configuration.
- Viewer: read-only access to explicitly permitted operational and analytics views.

## 4. Business information model

Business understanding is a first-class domain and must not be reduced to one system-prompt field.

### 4.1 Business Profile

Structured information includes:

- Display identity and short description.
- Industry/category.
- Supported languages and primary language.
- Timezone and working hours reference.
- Primary goals.
- Target audiences.
- Offerings: products and services at a summary level.
- Brand voice: formality, warmth, humor, response detail, and prohibited style.
- Contact and operational summary.
- Version, update time, and provenance.

The initial persistence design should use explicit typed columns/entities for stable concepts. Flexible metadata may use JSON only where the shape is genuinely provider- or industry-specific.

### 4.2 Setup Readiness

Readiness is calculated from required evidence rather than a decorative percentage. It reports blocking and optional tasks separately:

- Business Profile completed.
- At least one active Agent with a published prompt.
- At least one ready knowledge source or an explicit no-knowledge decision.
- Rules and human-handoff behavior confirmed.
- At least one channel connected and healthy.
- Required test scenarios passed or explicitly acknowledged.

The dashboard presents the next action for every incomplete item.

## 5. Business modules and user-facing behavior

### 5.1 Setup Center

A guided onboarding flow collects missing Business Profile fields, recommends the next setup task, and resumes from the last completed step. Imports may suggest values, but the Business Admin confirms them.

### 5.2 Agents

The page name is `Agents` / `ایجنت‌ها`.

The primary action is `Add Agent` / `افزودن ایجنت`, opening a large guided dialog with:

1. Name, purpose, audience, languages, and channels.
2. Tone, formality, humor, response detail, and sales posture.
3. Boundaries, prohibited behavior, and human-handoff conditions.
4. Generated instruction preview plus an advanced editable system-instruction field.
5. Save as draft, test, publish, and rollback-aware version history.

Field semantics:

- Agent name: operational display name.
- Short description: human-facing summary; not automatically sent to the model.
- Instruction version name: human-facing label for a prompt version.
- System instruction: versioned runtime instruction subordinate to platform and Business policy.

All Agents initially receive access to all ready knowledge bases within the same Business. The backend preserves an explicit authorization boundary so selective access can be enabled later without redesign.

### 5.3 Connections

Connections hold authenticated relationships with external systems. Initial provider types:

- WordPress.
- Google Account, with separately granted Drive and Sheets capabilities.
- YouTube Data capability, using API key for permitted public data or OAuth when private/account access is required.

A connection exposes label, provider, authorization method, scopes/capabilities, health, last test, last successful use, reconnect, and revoke. Secret material is resolved only by trusted server-side infrastructure.

Google Drive, Google Sheets, and YouTube are distinct capabilities even when one Google OAuth relationship is reused.

### 5.4 Knowledge

Knowledge contains relatively stable, attributable Business information.

Initial upload types:

- PDF.
- XLSX.
- DOCX.

Planned synchronized source types:

- WordPress posts, pages, and selected product/catalog content.
- Selected Google Drive files.
- Selected Google Sheets worksheets/ranges.
- Selected YouTube video metadata and approved transcript/caption content where permitted.

Each source displays source type, provenance, version, freshness mode, last sync, next sync, processing status, document/chunk counts, and actionable errors.

Processing lifecycle:

1. Validate authorization, file type, size, media safety, and tenant ownership.
2. Persist the original object in S3-compatible storage.
3. Create an immutable source/document version and enqueue ingestion.
4. Extract structured text and preserve page, sheet, range, heading, URL, and external identifiers.
5. Normalize and chunk content without discarding source provenance.
6. Generate embeddings through the configured `knowledge_embedding` AI task route.
7. Store text, metadata, hashes, and vectors in tenant-scoped PostgreSQL/pgvector records.
8. Validate counts and mark the new version ready.
9. Atomically activate the new version; keep the previous active version until replacement succeeds.
10. Retrieve only within the authenticated Business and return source references with model context.

No standalone "vector database file" is produced. Original files remain in object storage; searchable chunks and embeddings live in PostgreSQL/pgvector.

### 5.5 Live Tools

Tools provide live or transactional truth and must not be conflated with indexed knowledge. Initial tools are read-only and can retrieve examples such as current price, inventory, order status, or approved spreadsheet values.

Each tool declares schema, credential binding, allowed hosts, timeout, risk, rate limit, cache policy, audit behavior, and Agent permission.

Freshness modes are configured by a Business Admin, not requested from the end customer during a conversation:

- Live/no cache.
- 15 minutes.
- 1 hour.
- 1 day.
- Custom bounded duration.

Mutating tools are a later scope and require preview, authorization, and human approval according to risk.

### 5.6 Rules and Controls

The page name is `Rules & Controls` / `قوانین و کنترل‌ها`.

The default UI uses scenario-based controls:

- When AI may answer autonomously.
- Working hours and outside-hours behavior.
- Explicit human-request handling.
- Low-confidence, repeated-failure, complaint, sensitive-topic, and tool-failure escalation.
- Channel/message-type restrictions.
- Response limits and prohibited actions.
- Human takeover mode and conditions for returning control to AI.

These controls compile into deterministic backend policy. Natural-language guidance may supplement them but cannot replace authorization and enforcement.

### 5.7 Channels

Channels are adapters. Initial operational setup includes Telegram and Website Chat. Connection health, webhook status, capabilities, accepted inbound types, assigned Agent, and test action are visible.

### 5.8 Contacts

Contacts originate from normalized channel identities or authorized manual/import flows. The page shows identity, channels, language, lifecycle state, last activity, open conversation, and authorized memory summary. With no contacts, the page explains that contacts appear after channel traffic or import and links to channel setup.

### 5.9 Conversations

Conversations show normalized messages across channels, assigned Agent/operator, AI-versus-human control state, status, timestamps, source channel, and trace links where permitted. With no conversations, the page links to channel setup and test messaging.

### 5.10 Operator Inbox

The inbox is the actionable queue for human-controlled or escalated conversations. It supports queue filters, claim, release, assign, reply, resolve, return to AI, and assist-only suggestions. Empty state explains handoff triggers and links to Rules & Controls.

### 5.11 Analytics

Analytics is distinct from the setup dashboard. It reports conversation volume, resolution and handoff rates, unanswered topics, knowledge gaps, source health, Agent quality, latency, token usage, cost per conversation, and balance trends. It must not duplicate the overview page.

## 6. Context and prompt stack

Runtime context order is:

1. Non-overridable platform safety policy.
2. Published Business rules and controls.
3. Published Agent instruction version.
4. Channel-specific instruction.
5. Conversation state and human-handoff state.
6. Authorized customer context and structured memory.
7. Retrieved tenant-scoped knowledge with provenance.
8. Authorized tool context/results.
9. Current user input.

Every AI execution trace records the effective prompt version, task route, provider/model, usage, estimated charge, retrieval references, tool calls, latency, and permitted error details.

## 7. Configuration Assistant — later phase

The Configuration Assistant is a guided interview, not unrestricted chat. It asks only unresolved questions, produces a structured proposal, and creates drafts that require Business Admin approval.

Owner configuration includes:

- Dedicated `configuration_assistant` AI task route.
- Provider/model/fallback selection.
- Input/output pricing in the platform's Rial base currency.
- Versioned system instructions.
- Deterministic guardrails.
- Per-session and per-Business limits.
- Audit and usage records.

It cannot request or receive raw credentials, publish configuration, bypass rules, or access unrelated tenant/customer data.

## 8. Security and tenancy invariants

- Every Business-owned record is tenant-scoped in storage and server-side queries.
- Client-supplied tenant identifiers are never sufficient authorization.
- Credentials use encrypted server-side bindings and redacted audit events.
- Synchronized URLs require scheme/host allowlisting and SSRF protection.
- File ingestion includes type/size validation and safe parsing.
- Jobs are idempotent and retry-safe.
- Old knowledge versions remain active until a replacement is fully ready.
- AI output is untrusted and schema-validated before machine use.
- Privileged, credential, policy, prompt publication, and mutating operations are audited.

## 9. Implementation sequence

### Package 0 — Restore a trustworthy baseline

- Reconcile SQLAlchemy metadata with migration `0013`.
- Prove clean-database upgrade and `alembic check`.
- Run backend, web, and Docker validations.
- Do not add product behavior.

### Package 1 — Business Profile and Setup Readiness

- Add the Business Profile domain, migration, tenant-scoped API, RBAC, audit, and tests.
- Add Setup Center and readiness-driven overview UI in Persian and English.
- Keep AI assistance out of this package.

### Package 2 — Operational pages over existing APIs

- Implement functional Contacts, Conversations, and Operator Inbox views.
- Add actionable empty states and loading/error states.
- Preserve server-side RBAC and tenant isolation.

### Package 3 — Agent configuration UX

- Rename the page to Agents / ایجنت‌ها.
- Replace the inline action with the guided Add Agent dialog.
- Add structured behavior fields, prompt preview, testing, and version lifecycle UI.

### Package 4 — Managed Connections

- Add the canonical connection domain and encrypted credential lifecycle.
- Implement WordPress and Google authorization/health surfaces.
- Keep provider adapters separate from core domain logic.

### Package 5 — Knowledge expansion

- Add DOCX ingestion.
- Add connection-backed source selection and scheduled/on-demand sync.
- Implement default Business-wide Agent access while preserving future selective authorization.

### Package 6 — Live read-only tools

- Add approved WordPress/Sheets live reads, cache modes, schema mapping, health tests, and audits.

### Package 7 — Rules & Controls UX

- Map deterministic policy capabilities into plain-language scenarios and previews.

### Package 8 — Configuration Assistant

- Add Owner configuration, guided Business interview, structured proposals, draft-only application, usage charging, and evaluation tests.

## 10. Quality gates for every package

- Written acceptance criteria and explicit out-of-scope list.
- Migration and downgrade considerations where persistent data changes.
- Server-side RBAC and negative tenant-isolation tests.
- API schema validation and stable error codes.
- Audit and usage behavior where privileged or AI operations occur.
- Persian RTL and English LTR UI coverage.
- Loading, empty, error, and success states.
- Backend tests without paid external AI calls.
- Web lint, typecheck, and production build.
- Docker Compose build and relevant health checks.
- Review by the project lead before merge or push.

## 11. First implementation instruction — prepared, not dispatched

The first coding instruction is Package 0 only: repair the known migration metadata drift without adding a new migration unless the diff proves one is necessary; validate on a disposable database; run the complete relevant test/build gates; change no product behavior; and return a review-ready diff without commit, merge, or push.

Dispatch is blocked until the local command environment can run tests reliably.
