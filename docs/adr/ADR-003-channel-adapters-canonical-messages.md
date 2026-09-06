# ADR-003: Channel Adapters and Canonical Messages

**Status:** Accepted

## Context

Telegram, Website Chat, WhatsApp, Instagram, and future channels expose different webhook payloads, message types, identifiers, delivery APIs, and media handling.

If conversation and AI logic consumes channel-native structures, every new channel would spread conditional logic through the core application.

## Decision

Every external channel is implemented as a connector/adapter around a channel-independent conversation core.

Inbound channel payloads must be authenticated/validated and normalized into a canonical internal message contract before entering core processing.

Connectors declare their supported capabilities, such as text, voice, image, document, location, buttons, or typing indicators. Tenant configuration determines which supported inbound capabilities are enabled.

Channel adapters own external transport details only. They do not own AI or business decision logic.

## Alternatives Considered

### Separate end-to-end workflow per channel

Rejected because it duplicates customer identity, conversation, AI, tool, RAG, audit, and policy behavior.

### Shared core with raw channel payloads

Rejected because the core would still depend on external channel schemas and accumulate channel-specific branching.

## Consequences

Positive:

- New channels can reuse conversation/agent behavior.
- Core tests can operate on canonical messages.
- Channel capability policies become explicit.
- Transport failures remain isolated to connector boundaries.

Trade-offs:

- The canonical contract must evolve carefully to support richer channel capabilities.
- Some channel-specific metadata must be preserved separately without contaminating the core schema.

## Follow-up

- Define final canonical Pydantic message schemas.
- Define connector interface and capability registry.
- Define inbound idempotency keys.
- Define media reference and outbound delivery contracts.
