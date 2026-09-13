# Platform Owner, Business Administration, and Global AI Settings

## Goal

Provide one platform-wide Owner account that can administer Businesses, their users, and the
OpenAI/Google Gemini connections and model routing used by every Business.

## Product terminology

- **Business** is the product-facing name for the existing internal `Tenant` boundary.
- **Platform Owner** is a global role and is not a Business role.
- Business roles exposed by the administration UI are `Admin`, `Supervisor`, `Agent`, and
  `Viewer`.

## Scope

- Mark the bootstrapped account as Platform Owner.
- Allow the Platform Owner to list, create, activate, and deactivate Businesses.
- Allow the Platform Owner to list, create, activate, and deactivate Business users.
- Keep public registration disabled.
- Store OpenAI and Google Gemini API keys encrypted at rest and never return them from an API.
- Provide non-generative connection checks and live model discovery.
- Allow manual model identifiers when discovery is incomplete.
- Configure global model routes for the existing AI tasks. These routes apply to every Business.
- Configure effective-dated selling rates in Rial per model for one million input tokens, one
  million output tokens, and one minute of audio.
- Maintain a Business wallet and an append-only transaction ledger.
- Allow the Platform Owner to add manual wallet credit.
- Keep Rial as the canonical storage/calculation unit and let the Platform Owner choose whether
  Business-facing panels display balances as Rial or Toman.
- Attribute successful AI usage and its calculated Rial charge to the originating message where
  a message context exists.
- Load Vazirmatn locally in the Admin Console.

## Out of scope

- Image generation and image-generation model routing.
- Tenant/Business-specific or per-user AI overrides.
- Online payments, subscription plans, invitations, password reset, MFA, or SSO.
- Public signup and self-service Business creation.
- Deleting Businesses or users.

## Authorization

- Only a Platform Owner may access `/api/v1/platform/*`.
- Business members cannot read provider credentials or global AI configuration.
- Deactivating a Business member revokes their active sessions.
- API responses expose only whether a provider credential is configured, never the credential.

## AI provider behavior

- Providers are `openai` and `gemini` (Google Gemini Developer API).
- Connection checks use provider model metadata/list endpoints and do not generate paid content.
- Discovered models are filtered by task capability where metadata or a conservative local
  capability classification is available.
- A manually entered model identifier remains available because provider model inventories and
  access permissions change over time.
- Database credentials take precedence over deployment environment variables; environment
  variables remain a backward-compatible fallback.

## Usage billing

- UI rates use Rial per-million token units to avoid impractical fractional per-token values.
- Input and output rates are separate because provider/model prices differ by direction.
- Voice usage is priced per minute because transcription usage is time-based.
- Price changes are effective-dated; historical charges are never recalculated with a new rate.
- Wallet credits are manual Owner actions in this phase and are always audited.
- Calculated charges are rounded up to the nearest Rial, the system's smallest billing unit.
- Toman display divides the canonical Rial amount by ten and never rewrites stored ledger values.
- AI debits are idempotent by execution trace and keep the related message identifier when known.
- When a priced model is configured, a Business with a non-positive wallet balance cannot start a
  new billable AI call. A final concurrent call may create a small negative balance; strict
  reservation and payment authorization are deferred with the online-payment milestone.
- A platform-wide task route without a selling rate is blocked with
  `model_pricing_not_configured`; it is never treated as free usage.

## Acceptance criteria

- The existing bootstrap owner is recognized as Platform Owner after migration.
- Owner-only navigation shows Businesses, Users, and Settings.
- The Owner can create a Business and create a user with one of the four supported Business roles.
- The Owner can activate/deactivate a Business user and a Business.
- A non-owner receives `403` from every platform administration endpoint.
- Saved AI keys are encrypted, redacted from API responses and audit records, and usable by both
  the API and worker without a restart.
- OpenAI and Gemini connections can be checked and their accessible models can be listed.
- Global AI task routes are used for all Businesses, with legacy per-Business routes retained only
  as a compatibility fallback until a global route is configured.
- Owner can define model selling rates, credit a Business wallet, and inspect its transaction
  ledger.
- Successful priced AI executions create exactly one debit transaction and retain per-message
  token/audio usage attribution.
- English remains LTR, Persian remains RTL, and both use the bundled local Vazirmatn variable font.
- Backend tests, frontend lint/type checks, migrations, and Docker builds pass.
