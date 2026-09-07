# ADR-013 — Website Chat Public Session and Polling Transport

## Status

Accepted

## Context

Milestone 7 introduces the first browser-originated public channel. Unlike Telegram, there is no
external provider webhook or bot credential that authenticates inbound traffic. A browser widget
runs on a tenant-controlled website, needs a narrowly scoped public identity, and must not inherit
Admin Console authentication or expose a tenant-wide secret.

The first Website transport also needs outbound delivery. WebSocket or SSE infrastructure would
add connection lifecycle, proxy, scaling, and retry concerns before the product requires realtime
streaming semantics.

ADR-007 already requires Customer Webchat to remain a separate lightweight frontend artifact and
to use the shared backend conversation/channel contracts.

## Decision

### Public trust boundary

A Website channel account owns an exact list of allowed browser origins. Public Website requests
resolve the channel account server-side and require an exact `Origin` match. Wildcards are not
accepted.

A public session is authenticated by a cryptographically random opaque session token returned
once to the browser. PostgreSQL stores only the SHA-256 digest. The session is scoped to one
tenant/channel account, has a bounded expiration, and owns an anonymous visitor UUID.

The browser is not allowed to self-assert a trusted customer/CRM identity. A future known-customer
upgrade must use a server-verifiable assertion or other trusted integration boundary.

### Canonical processing

Website inbound text is normalized to the existing `CanonicalInboundMessage` and uses the same
channel persistence, Redis Streams worker, Agent assignment, Prompt, AI Gateway, AgentRun, and
canonical Message flow as Telegram.

No Website-specific AI runtime is introduced.

### Outbound delivery

Website outbound text is considered delivered to the Website transport when it is durably stored
as the canonical outbound Message under the existing dispatch idempotency lock. The browser reads
messages through the public session endpoint.

Milestone 7 uses bounded polling rather than WebSocket/SSE. This keeps the initial deployment
compatible with ordinary HTTP reverse proxies and avoids introducing a connection broker or
sticky-session assumptions. A future push transport may replace polling without changing Agent or
Conversation semantics.

### Public client artifact

The first client lives in `apps/webchat` and is dependency-free browser JavaScript. It is separate
from the Next.js Admin Console and communicates only with the public Website transport API.

## Alternatives Considered

### Public tenant API key embedded in the widget

Rejected. A long-lived tenant/channel secret embedded in public JavaScript is not a secret and
would create a broad replay/abuse credential.

### Trust Origin without a per-session token

Rejected. Origin validation limits browser origins but is not an end-user/session identity and is
insufficient for protecting conversation reads and writes.

### Cookie-based cross-site session

Rejected for the initial widget because third-party cookie restrictions vary by browser and embed
topology. An explicit scoped token is more predictable for an embeddable client.

### WebSocket or SSE in Milestone 7

Deferred. Both are viable future transports, but polling is sufficient to prove omnichannel core
reuse without adding unnecessary connection infrastructure.

## Consequences

Positive:

- Website Chat reuses all canonical backend business logic.
- Public credentials are session-scoped rather than tenant-wide.
- Exact origins are explicit tenant configuration and enforcement data.
- Deployment remains simple HTTP + PostgreSQL + Redis.
- The public widget remains small and independent of the Admin Console.

Trade-offs:

- Polling has higher request overhead and response latency than a push connection.
- Session tokens live in browser storage when persistence is enabled by the embedding site.
- Exact origin configuration requires operators to register every supported scheme/host/port.
- Production abuse/rate controls still need the later hardening/policy work.

## Follow-up Implications

- M11 Human Handoff can surface human outbound messages through the same Website polling API.
- A later SSE/WebSocket transport must preserve the same canonical Message source of truth.
- Known-customer Website identity must use a trusted server-side assertion design; arbitrary
  browser identity fields must not become authorization evidence.
- Production rate limiting should key on channel account, origin, session, and network signals
  without placing Redis-only state above PostgreSQL conversation truth.
