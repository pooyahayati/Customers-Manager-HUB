# Milestone 16 — Commercial MVP Validation

## Goal

Prove that Customers Manager HUB can be installed and demonstrated as a reusable multi-tenant business platform without a tenant-specific source-code fork.

M16 is a validation milestone. It does not introduce a new product subsystem.

## Acceptance model

The M16 gate combines three existing validation layers instead of duplicating infrastructure:

1. **Docker and Compose** proves a clean deployment can build, migrate, start, become ready, survive a load probe, restore PostgreSQL data, and run application services as non-root users.
2. **Commercial MVP integration harness** executes the complete PostgreSQL/Redis integration regression and maps the tested capabilities to the required commercial scenario.
3. **Security** proves locked Python/Node dependencies, repository secrets, and runtime container filesystems pass the production security gates.

External AI/channel/tool calls are deterministic in CI through provider/channel/tool test adapters. Real OpenAI, Gemini, Telegram, and live business credentials are validated through the operator smoke checklist and are never committed to the repository.

## Required scenario and automated evidence

| # | Required capability | Automated evidence |
|---|---|---|
| 1 | Start from documented Docker setup | `CI / Docker and Compose` |
| 2 | Create a tenant | `test_tenant_auth_integration.py` |
| 3 | Configure users/roles | `test_tenant_auth_integration.py` |
| 4 | Configure OpenAI/Gemini credentials | typed runtime config + provider contract/unit gates; real-secret smoke is operator-only |
| 5 | Select models by AI task | `test_ai_gateway_integration.py`, `test_agent_prompt_integration.py` |
| 6 | Connect Telegram | `test_telegram_channel_integration.py` |
| 7 | Configure Website Chat | `test_website_chat_integration.py` |
| 8 | Enable text/voice channel capabilities | Telegram/Website/voice integration suites |
| 9 | Upload PDF/Excel knowledge | `test_knowledge_integration.py` |
| 10 | Configure a live business API tool | `test_tool_integration.py` |
| 11 | Configure/publish an agent and prompt | `test_agent_prompt_integration.py` |
| 12 | Receive text and voice messages | Telegram/Website/voice integration suites |
| 13 | Use RAG and live tools appropriately | knowledge/tool integration suites |
| 14 | Respond through originating channel | agent/tool/knowledge/voice integration suites |
| 15 | Escalate to a human | `test_handoff_integration.py`, policy integration |
| 16 | Preserve contacts/conversations/memory | customer conversation + memory integration suites |
| 17 | Display trace, audit, usage, and core analytics | AI/tool/knowledge traces + `test_analytics_integration.py` |

The harness intentionally runs the full `test_*_integration.py` regression so the commercial gate cannot silently omit a newly added integration invariant.

## Provider credential boundary

OpenAI and Gemini API keys are deployment secrets loaded through runtime environment configuration. Tenant-specific model selection remains data-driven through AI task profiles. Route parameters reject secret-like keys.

M16 does not move provider secrets into tenant-owned database records because that is not required by the current architecture or roadmap acceptance gate. A future BYOK-per-tenant product requirement would require an explicit product/security design rather than being hidden inside validation work.

## Fresh-install validation command

From a configured repository clone:

```bash
make mvp-validate
```

The local validator:

- starts PostgreSQL and Redis if needed;
- creates a disposable `cmh_mvp_validation` database;
- uses Redis database 15;
- applies all migrations and checks for Alembic drift;
- executes the complete integration regression through the commercial validation harness;
- removes only the disposable validation database/Redis state when finished.

It does not truncate or reuse the normal `customers_manager_hub` development database.

## Real credential smoke

A controlled deployment may additionally validate:

- `OPENAI_API_KEY` and/or `GOOGLE_GEMINI_API_KEY`;
- Telegram bot token and webhook delivery;
- configured Website Chat origin;
- one tenant-owned live business API credential;
- one representative PDF/XLSX upload to configured S3-compatible storage.

These checks are environment-specific and must never require repository changes or committed secrets.

## Exit gate

M16 is complete when:

- `make mvp-validate` is documented and executable against a disposable local validation state;
- persistent CI runs the commercial integration harness;
- Docker/Compose and Security gates pass on the same PR head;
- the 17-step scenario is mapped to automated and controlled-smoke evidence;
- no tenant-specific source fork, hard-coded tenant identifier, or tenant-specific infrastructure service is required.
