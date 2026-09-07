# Milestone 12 — Customer Memory & Intelligence

## Status

Implementation specification for M12.

## Goal

Create structured, tenant-scoped customer memory that can improve future responses without treating arbitrary model inference as verified fact.

## Scope

- Structured customer memory items attached to canonical Contacts.
- Explicit distinction between customer-stated facts and model inference.
- Provenance, confidence, observation time, freshness/expiry, and verification metadata.
- Provider-independent extraction through the existing `customer_memory_extraction` AI task profile.
- Idempotent extraction from canonical inbound customer messages.
- Deterministic customer-context selection for Agent Runtime.
- Preferred language.
- Response detail preference.
- Product interests.
- Lead/lifecycle status foundation.
- Previous issues and resolutions.
- Tenant-scoped API visibility plus create/edit/verify/delete controls.
- Audit events for manual memory mutations.
- Integration with Telegram and Website Chat through the existing canonical conversation model.

## Out of Scope

- Free-form model-written customer biographies.
- Sensitive-trait profiling.
- Identity merging based on memory.
- Cross-tenant memory.
- Vector search over customer memory.
- Separate memory microservice or new queue infrastructure.
- Marketing automation or campaign targeting.
- Full CRM lifecycle automation.

## Domain Model

### CustomerMemoryItem

Each item is a structured signal with:

- `tenant_id`
- `contact_id`
- `category`
- `value`
- `evidence_kind`: `fact` or `inference`
- `confidence` in `[0, 1]`
- `source_type`: `customer_message` or `manual`
- optional `source_message_id`
- optional `source_conversation_id`
- optional `created_by_user_id`
- `observed_at`
- optional `expires_at`
- optional verification metadata (`verified_at`, `verified_by_user_id`)
- soft deletion metadata (`deleted_at`, `deleted_by_user_id`)
- timestamps

Supported categories:

- `preferred_language`
- `detail_level`
- `product_interest`
- `lifecycle_status`
- `issue`
- `resolution`

No unrestricted arbitrary category is accepted from the model.

### CustomerMemoryExtraction

A small extraction marker records successful processing of a source message, including the zero-item case. A unique `(tenant_id, source_message_id)` constraint makes extraction idempotent under worker retries.

## Fact vs Inference Rule

- `fact` means the customer explicitly stated the information in the source message/context.
- `inference` means the model derived a likely preference/status without an explicit customer statement.
- Automatic Agent personalization may use active, fresh facts above the confidence threshold.
- Unverified inferences are stored and visible but are not automatically injected into Agent context.
- A tenant operator can explicitly verify an inference, after which it becomes eligible for context selection while remaining labeled as an inference.

## Sensitive Data Rule

Extraction instructions prohibit deriving or storing sensitive traits such as health/medical status, race/ethnicity, religion, political affiliation, sexual orientation/sex life, criminal history, or equivalent unnecessary sensitive profiling.

The structured category allowlist also prevents arbitrary biography expansion.

## Freshness Policy

Freshness is application-controlled, not chosen by the model.

Default expiry windows:

- preferred language: 180 days
- detail level: 180 days
- product interest: 90 days
- lifecycle status: 30 days
- issue: 180 days
- resolution: 365 days

Manual entries may set an explicit expiry or remain non-expiring when appropriate.

## Extraction Runtime

For a canonical inbound customer text/voice message:

1. Resolve tenant, conversation, contact, and source message.
2. Skip when an extraction marker already exists.
3. Build bounded recent conversation context.
4. Call AI Gateway with `AITaskType.CUSTOMER_MEMORY_EXTRACTION` and a strict JSON schema.
5. Validate category, evidence kind, confidence, and value length.
6. Apply deterministic freshness policy.
7. Upsert singleton categories and deduplicate repeatable categories.
8. Persist extraction marker in the same transaction as memory writes.

Extraction is best-effort enrichment after the primary conversation/handoff path. A memory extraction failure must not cause duplicate customer responses.

## Context Selection

Agent Runtime resolves the Contact through the Conversation and selects only:

- active, non-deleted items;
- items not past `expires_at`;
- facts meeting the confidence threshold; and
- verified inferences.

The rendered context must preserve trust labels, for example:

- `CUSTOMER-STATED FACT`
- `VERIFIED FACT`
- `APPROVED INFERENCE`

Memory context is treated as untrusted customer context, never as platform instructions.

A bounded character budget prevents memory from crowding out the current conversation.

## API

Base path:

`/api/v1/tenants/{tenant_id}/contacts/{contact_id}/memories`

Required operations:

- `GET /memories` — list visible active/deleted memory items with filters.
- `POST /memories` — create a manual verified memory item.
- `PATCH /memories/{memory_id}` — edit value/category/freshness and verify/unverify where allowed.
- `DELETE /memories/{memory_id}` — soft-delete the item.

All lookups are tenant-scoped. Mutations require the existing contact write roles and produce AuditEvent records.

## Runtime Integration

- Agent Runtime adds `[CUSTOMER MEMORY]` after conversation policy/state and before RAG/tool context.
- Worker runs best-effort memory extraction after the primary message outcome has been decided/sent.
- Handoff conversations still receive memory extraction from inbound customer messages; memory extraction does not resume autonomous AI.
- No channel adapter imports memory business logic.

## Security

- Server-side tenant isolation on every memory query/update.
- No model provider-specific imports in memory business logic.
- No raw message metadata or credentials copied into memory.
- Structured category allowlist.
- Soft-delete for customer memory; audit privileged changes.
- Unverified inference is never silently promoted to fact.
- Default tests use mocked AI provider behavior.

## Acceptance Criteria

M12 is complete when:

1. A returning Contact can accumulate structured memory from canonical customer messages.
2. Extracted facts and inferences remain distinguishable in storage and API responses.
3. Provenance, confidence, freshness, and verification are retained.
4. Duplicate worker delivery does not duplicate extraction work or memory items.
5. Expired/deleted items are excluded from automatic Agent context.
6. Unverified inference is excluded from automatic Agent context.
7. Verified inference remains labeled as inference when injected.
8. Preferred language, detail preference, product interest, lifecycle status, issues, and resolutions are representable.
9. Tenant operators can view, create, edit, verify, and soft-delete memory items according to RBAC.
10. Cross-tenant access tests fail safely.
11. Agent Runtime can use selected memory on Telegram and Website Chat without channel-specific logic.
12. Ruff, formatting, strict Pyright, unit/integration tests, Alembic migration/drift checks, and Docker/Compose validation pass.
