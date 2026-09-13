# ADR-022 — Platform Owner, Businesses, and Global AI Configuration

## Status

Accepted

## Context

The product will be sold to multiple independent customers. The existing multi-tenant boundary is
correct, but the user-facing product calls each tenant a **Business**. A platform-wide operator must
provision Businesses and users while keeping Business data isolated. The same operator currently
funds and controls the OpenAI and Google Gemini connections used by all Businesses.

The existing AI Gateway already routes by task but reads provider credentials from process
environment variables and stores task profiles per tenant. That cannot support secure runtime
configuration from the Admin Console or one global policy applied consistently to every Business.

## Decision

1. Add an explicit `is_platform_owner` identity attribute. Platform-owner access is enforced by a
   dedicated server-side dependency and is the only authorization accepted by platform routes.
2. Keep the database and API term `tenant` internally to avoid a risky domain-wide rename. Present
   it as `Business` in the Admin Console and new platform APIs.
3. Expose only `Admin`, `Supervisor`, `Agent`, and `Viewer` when provisioning Business members.
   Existing legacy tenant roles remain readable for migration compatibility but cannot be assigned
   through the new platform administration API.
4. Store global provider credentials in dedicated encrypted records using AES-256-GCM and the
   deployment `ENCRYPTION_KEY`. Bind provider identity and key version as authenticated data.
5. Never return credentials. Return redacted configuration status and bounded connection-test
   results only.
6. Store platform AI task profiles separately from tenant task profiles. The AI Gateway resolves a
   global profile first and uses a legacy tenant profile only when no global profile exists.
7. Resolve provider credentials at request/job execution time so API and worker processes observe
   changes without restarts. Environment variables remain a fallback for deployments that have not
   migrated to database-managed credentials.
8. Do not implement image generation in this change.
9. Add a platform-priced, Rial-denominated usage layer. Rates are effective-dated per
   provider/model and split into input-token, output-token, and audio-minute components.
10. Use an append-only Business wallet ledger. Owner credits are manual and audited; successful AI
    execution debits are idempotent by execution trace and retain message attribution when
    available.
11. Enforce a non-positive-balance stop before priced calls. Full funds reservation and payment
    gateway integration are deferred because actual output usage is unknown before generation.
12. Store all monetary values in Rial. A single Owner-controlled presentation setting may display
    Business-facing balances in Rial or Toman; this setting never changes ledger arithmetic.
13. Reject a platform-wide AI route when its provider/model has no effective selling rate, rather
    than silently allowing unbilled usage.

## Consequences

Positive:

- The commercial hierarchy is explicit: Platform Owner -> Business -> Business member.
- Provider secrets remain server-side, encrypted, auditable, and dynamically replaceable.
- One routing policy is consistently applied to every Business.
- Future Business-specific overrides have an explicit lower-priority extension point.
- Selling prices and wallet movements remain historically traceable without rewriting balances.

Trade-offs:

- The Platform Owner is a privileged cross-tenant identity and requires focused negative tests.
- Model-list metadata is not a complete capability contract for every provider, so the UI combines
  live discovery with conservative capability classification and manual IDs.
- Online payment, strict balance reservation, quota controls, and automated top-up remain required
  before public commercial launch.
- Concurrent calls can create a bounded negative balance until a reservation mechanism is added;
  the next priced call is blocked once the balance is non-positive.
