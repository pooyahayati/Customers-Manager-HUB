# Commercial MVP Validation Runbook

This runbook validates the supported MVP as a reusable platform. It separates deterministic repository validation from environment-specific external-provider smoke tests.

## 1. Fresh clone

Prerequisites:

- Docker with Compose v2
- Python/toolchain defined by `.python-version`
- `uv`
- configuration copied from `.env.example`

```bash
cp .env.example .env
make infra-check
make up
make ps
```

Confirm:

- API readiness: `http://127.0.0.1:8000/ready`
- Admin Console: `http://127.0.0.1:3000/`

PostgreSQL, Redis, and the S3-compatible object-storage port bind to host loopback by default. Application containers use the private Compose network for service-to-service access; do not expose infrastructure ports publicly unless the deployment has an explicit network-security design.

Do not commit `.env` or real credentials.

## 2. Deterministic commercial validation

Run:

```bash
make mvp-validate
```

The command uses a disposable PostgreSQL database named `cmh_mvp_validation` and Redis DB 15. The normal development database is not used by the integration regression.

The validation covers tenant/RBAC, AI task routing, Telegram, Website Chat, agents/prompts, tools, knowledge/RAG, voice transcription, human handoff, memory, policies, traces/audit, and analytics.

A failure is a release blocker for the commercial MVP unless the failing contract has first been intentionally changed in the roadmap/specification.

## 3. AI provider credentials

OpenAI and Gemini credentials are deployment secrets, not AI route parameters.

Configure one or both in the deployment environment:

```text
OPENAI_API_KEY=...
GOOGLE_GEMINI_API_KEY=...
```

Then configure tenant AI task profiles through the API/Admin surface. Provider/model selection is tenant-scoped; the secret remains server-side.

For a controlled real-provider smoke test, verify at least one configured task can complete through its selected provider. Never print or persist the raw API key in logs, audit details, prompts, traces, or route parameters.

## 4. Telegram

For the validation tenant:

1. Create a Telegram channel account.
2. Provide its bot token through the channel configuration API.
3. Enable required inbound capabilities (`text`, `voice` where applicable).
4. Confirm webhook registration uses the deployment's HTTPS `TELEGRAM_WEBHOOK_BASE_URL`.
5. Send one text message and one voice message.
6. Confirm both enter the same canonical contact/conversation/message model.
7. Confirm the reply is delivered through Telegram.

## 5. Website Chat

1. Create/configure a Website Chat channel.
2. Configure allowed site origin(s).
3. Start an anonymous visitor session.
4. Send a message through the public website-chat API/widget.
5. Confirm the message enters the canonical conversation runtime and the response returns through Website Chat.

## 6. Knowledge and live business data

For the same tenant:

1. Create a knowledge base/source.
2. Upload one representative PDF and one XLSX file.
3. Allow ingestion/embedding to complete.
4. Grant the agent knowledge access.
5. Configure one live business API tool and server-side credential.
6. Grant that agent the tool.
7. Ask one question that should use RAG and one that should require live business data.
8. Confirm knowledge/tool traces retain provenance while credentials are absent from model-visible data and logs.

## 7. Agent, prompt, handoff, and memory

1. Create and publish a prompt.
2. Create an agent and assign it to the Telegram/Website channel as required.
3. Confirm autonomous response works.
4. Trigger a human handoff and verify autonomous AI pauses.
5. Send a human response through the originating channel.
6. Resolve/cancel the handoff according to the scenario and optionally resume AI.
7. Return as the same customer and verify approved customer memory can be selected without treating unverified inference as fact.

## 8. Operational evidence

Confirm the tenant can inspect:

- conversation/contact history;
- AI execution traces;
- tool/knowledge traces;
- audit events;
- AI usage and estimated cost where pricing is configured;
- conversation, response, handoff, automation, and tool analytics.

## 9. Release evidence

The PR/head is commercially releasable only when all persistent workflows pass:

- **CI / Backend static and unit**
- **CI / Backend integration** — invokes the commercial MVP integration harness
- **CI / Frontend**
- **CI / Docker and Compose**
- **Security / Dependency audit**
- **Security / Repository secret scan**
- **Security / Container vulnerability scan**

The external credential smoke checklist is deployment-specific. It must not require a tenant-specific code fork or repository secret.

## 10. Cleanup

The deterministic validator cleans its disposable validation database and Redis DB. Normal development data is not intentionally removed.

For a completely disposable fresh-install test environment only, `make clean` removes project containers and volumes. Do not run `make clean` against development data you intend to keep.
