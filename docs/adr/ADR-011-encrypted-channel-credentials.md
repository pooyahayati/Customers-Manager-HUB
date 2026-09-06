# ADR-011: Encrypted Channel Credentials

**Status:** Accepted

## Context

Milestone 5 introduces tenant-owned Telegram bot credentials. Bot tokens and webhook secrets are high-value secrets and must not be stored in plaintext, returned through Admin APIs, written to audit records, or placed in queue payloads.

A general external secret manager or tenant credential vault has not yet been selected. Deferring all credential persistence would make the Telegram channel difficult to configure and would not satisfy the reusable multi-tenant product model.

## Decision

Store channel credentials encrypted at rest in PostgreSQL using application-level AES-256-GCM authenticated encryption from the `cryptography` package.

A deployment-level master key is supplied through `ENCRYPTION_KEY` and represented as `SecretStr`. The master key must decode to exactly 32 bytes and is never persisted in PostgreSQL.

Each stored channel credential uses:

- a random 96-bit nonce;
- AES-256-GCM ciphertext including the authentication tag;
- associated authenticated data containing the credential version, tenant ID, channel-account ID, and credential kind;
- an explicit key/version field to keep future rotation/migration possible.

Credentials live in a dedicated tenant-owned `channel_credentials` table and are addressed by a constrained credential kind such as `telegram_bot_token` or `telegram_webhook_secret`.

Admin APIs may accept a new bot token over an authenticated HTTPS request, but they never return the token, webhook secret, nonce, or ciphertext. Audit events record only non-sensitive configuration facts.

The server generates Telegram webhook secrets itself using Telegram-compatible characters and stores them encrypted immediately.

## Alternatives Considered

### Plaintext database columns

Rejected because database compromise, backups, logs, or accidental query exposure would reveal long-lived bot credentials.

### Environment variable per Telegram account

Rejected as the primary product design because it makes tenant/channel provisioning deployment-specific, requires process restarts, and does not scale cleanly to multiple tenant-owned channel accounts.

### Full external secret manager / vault now

Deferred. It would add infrastructure and provider decisions beyond Milestone 5. The dedicated credential boundary keeps a future external-secret backend possible without changing channel-domain APIs.

### Reversible encryption with a static IV or unauthenticated cipher

Rejected because it would not provide adequate confidentiality and integrity guarantees.

## Consequences

Positive:

- Channel credentials are encrypted at rest.
- Credentials are isolated from normal channel metadata and API responses.
- Ciphertext swapping across tenants/accounts/kinds fails authentication because IDs and purpose are bound as AAD.
- The storage boundary can later be backed by an external secret manager.

Trade-offs:

- The deployment master key becomes a critical operational secret.
- Losing the master key makes encrypted channel credentials unrecoverable.
- Key rotation is not automated in Milestone 5.

## Follow-up Implications

- Production deployments must generate and protect a strong `ENCRYPTION_KEY` outside Git.
- Milestone 15 must document backup/restore and key-rotation procedures.
- Future provider/tool credentials should reuse this boundary only after their own specification confirms the same threat model.
