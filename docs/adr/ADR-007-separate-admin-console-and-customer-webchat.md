# ADR-007 — Separate Admin Console and Customer Webchat

## Status

Accepted

## Context

Customers Manager HUB has two browser-facing product surfaces with materially different users, performance requirements, security boundaries, and release lifecycles:

1. The **Admin Console**, used by tenant owners, administrators, supervisors, operators, and analysts to configure agents, channels, prompts, tools, knowledge, policies, conversations, handoff, analytics, and platform settings.
2. The **Customer Webchat / Responder**, embedded in a tenant's public website and used by end customers to communicate with the configured agent or human operator.

Treating both surfaces as one frontend application would unnecessarily couple admin authentication, internal UI dependencies, deployment cadence, and bundle size to the public customer chat experience.

A review of established conversational-AI product patterns reinforced that these surfaces should remain separately deployable concerns while sharing the same backend conversation and channel contracts.

## Decision

### Admin Console

`apps/web` is reserved for the **Admin Console**.

It may use Next.js and the frontend runtime/tooling defined by the technical baseline.

The current frontend runtime-skeleton work must remain intentionally minimal and must not implement the customer chat widget, responder UI, fake agent dashboards, or placeholder product modules.

### Customer Webchat / Responder

The customer-facing Website Chat client will be implemented later, during the Website Channel milestone, as a **separate lightweight frontend artifact** with its own clear runtime/build boundary.

Its final repository location will be selected only when implementation becomes imminent. Possible locations such as `apps/webchat` or a dedicated package are not created now.

The Webchat must:

- Be embeddable in third-party tenant websites.
- Support floating-widget and inline/embed usage where practical.
- Be brandable and tenant-configurable without tenant-specific source builds.
- Communicate only through channel-independent backend contracts for Website Chat.
- Preserve the same canonical conversation/message model used by other channels.
- Support explicit allowed-origin controls before public deployment.
- Keep public-client credentials and capabilities narrowly scoped.
- Be aware of conversation persistence and human-handoff state.
- Avoid coupling to Admin Console authentication, session state, internal routes, or UI dependencies.
- Keep browser payload and runtime dependencies small because it executes on customer websites.

### Configuration Boundary

Website Chat configuration should be grouped by product concern rather than exposed as arbitrary styling fields. The intended configuration areas are:

- Identity: display name, avatar, description, composer placeholder.
- Appearance: theme, primary visual settings, typography/radius where supported.
- Behaviour: welcome behavior, conversation persistence, attachments, sound, feedback when implemented.
- Deployment and security: embed mode, launcher behavior, allowed origins, and later rate-limiting/security controls.

Only settings required by an implemented capability should be added. This ADR does not authorize creating all of these settings during the runtime skeleton.

### Backend Boundary

This decision does **not** create a second conversation backend.

Admin Console and Customer Webchat use the same FastAPI application/domain modules and canonical conversation infrastructure. Website Chat remains a channel adapter/transport concern; AI reasoning, memory, policies, tools, knowledge, and handoff remain server-side platform concerns.

## Alternatives Considered

### One Next.js application for both Admin Console and Customer Webchat

Rejected because it couples internal administration concerns to a public embeddable client, increases payload and security surface, and makes independent deployment/versioning harder.

### Build tenant-specific chat applications

Rejected because per-tenant builds complicate deployment and operations. Tenant branding and behavior should be runtime configuration wherever practical.

### Create the Webchat package now as a placeholder

Rejected. The project should not contain empty future package trees. The concrete Webchat artifact will be created when Website Chat implementation starts.

## Consequences

Positive:

- Admin development can evolve without bloating the customer-facing widget.
- Public Webchat can be optimized aggressively for bundle size and embedding safety.
- Security boundaries are clearer.
- Website Chat can be deployed/versioned independently if needed.
- Multi-channel backend architecture remains unchanged.

Trade-offs:

- Two browser-facing build artifacts will eventually need release coordination.
- Shared visual primitives cannot be assumed; any future sharing must justify its dependency cost.
- Website Chat requires a deliberate public-client authentication/origin strategy during its milestone.

## Follow-up Implications

- Milestone 1 `apps/web` implementation is Admin Console runtime only.
- Website Chat must not be implemented on the current `feat/web-runtime-skeleton` branch.
- The Website Chat milestone must define the public client contract, tenant configuration schema, origin validation, persistence behavior, and handoff behavior before implementation.
- Human Inbox remains an Admin Console concern, while customer handoff state is reflected through the shared conversation backend.
- No new frontend framework, shared UI package, state-management library, or Webchat directory is implied by this ADR.
