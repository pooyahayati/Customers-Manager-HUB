# Milestone 4 — AI Gateway & Task Model Routing

## Goal

Add provider-independent AI execution infrastructure so application code selects an AI task, while the gateway resolves tenant configuration, provider/model routing, retry/fallback, usage, and execution traces.

## Scope

- Canonical AI task taxonomy for:
  - `customer_response`
  - `voice_transcription`
  - `intent_classification`
  - `conversation_summary`
  - `customer_memory_extraction`
  - `embedding`
- Canonical request/result contracts for:
  - text generation
  - structured JSON generation
  - text embeddings
  - audio transcription
- Runtime provider registry and adapter protocol.
- OpenAI REST adapter.
- Gemini REST adapter.
- Deterministic mock adapter for automated tests.
- Tenant-scoped task profiles with ordered provider/model routes.
- Provider/model parameters stored as bounded JSON configuration.
- Timeout and attempts-per-route configuration.
- Ordered fallback across configured routes.
- Normalized token/audio usage fields when a provider reports them.
- Append-oriented AI execution trace records without raw prompt, response, credential, or audio persistence.
- Admin APIs to read and configure tenant task profiles.
- PostgreSQL migration and integration tests.

## Out of scope

- Agent/prompt runtime.
- Telegram or Website Chat integration.
- Queue-based AI jobs.
- Tool calling/execution.
- RAG/retrieval.
- Cost calculation/pricing tables.
- Tenant-managed encrypted provider secret storage.
- Streaming responses.
- Provider model discovery UI.
- Smart cost/quality routing strategies.
- Persisting raw AI inputs/outputs.

## Architecture

### Provider independence

Business/application modules must not import OpenAI or Gemini request/response contracts. They call the internal gateway with canonical request types.

The provider registry maps stable provider keys (`openai`, `gemini`, and test-only `mock`) to adapters. Exact model identifiers are route configuration data.

### Credential policy

For Milestone 4, live adapters resolve platform-level credentials from process environment/settings. Credentials are never stored in task profiles or execution traces. Tenant-managed encrypted provider credentials require a dedicated secret-storage design and are deferred.

### Task profiles

Each tenant may have at most one active profile per task type. A profile contains:

- task type
- timeout in seconds
- attempts per route
- ordered routes

Each route contains:

- provider key
- model ID
- bounded provider/model parameters
- priority

The ordered route list is the fallback chain. The first successful route wins.

### Retry/fallback

- Each route is attempted up to `attempts_per_route`.
- Only failures classified as retryable are retried on the same route.
- After a route is exhausted or unavailable, the gateway tries the next route.
- Invalid task/profile configuration fails before provider execution where possible.
- The gateway returns a normalized error when all routes fail.

### Structured output

Machine-consumed generation accepts a JSON Schema through the canonical request. Adapters translate it into each provider's supported structured-output contract. The returned JSON is parsed and validated as JSON before being returned to application code. Domain-specific schema validation remains the caller's responsibility.

### Trace and privacy

Each provider attempt creates an append-oriented trace containing only operational metadata:

- tenant
- task type
- provider/model
- route priority and attempt number
- status
- provider request ID when available
- latency
- normalized usage fields
- normalized error code
- timestamps

Raw prompts, model output, audio bytes, API keys, and arbitrary provider error bodies are not persisted.

## Security

- All task-profile APIs are tenant-scoped server-side.
- Only `owner` and `admin` may modify AI task profiles.
- Other active tenant roles may read non-secret routing configuration.
- API keys must not appear in database rows, responses, logs, traces, or exception messages.
- Provider parameters reject sensitive credential-like keys and have a strict serialized size limit.
- Live provider calls must use configured timeouts.

## API surface

- `GET /api/v1/tenants/{tenant_id}/ai/task-profiles`
- `GET /api/v1/tenants/{tenant_id}/ai/task-profiles/{task_type}`
- `PUT /api/v1/tenants/{tenant_id}/ai/task-profiles/{task_type}`

No public AI execution endpoint is added in this milestone. Gateway execution is exercised through application/integration tests and is consumed by later Agent/Channel milestones.

## Provider HTTP contracts

Implementation is based on current official APIs, but endpoint/model details remain isolated inside adapters and model IDs remain configuration:

- OpenAI generation/structured output: Responses API.
- OpenAI embeddings: Embeddings API.
- OpenAI transcription: Audio Transcriptions API.
- Gemini generation/structured output: `models.generateContent`.
- Gemini embeddings: `models.embedContent`.
- Gemini transcription: configured transcription-capable model through `generateContent`, with bounded inline audio for this milestone.

## Acceptance criteria

1. A tenant can configure all required task types with ordered provider/model routes.
2. Cross-tenant profile access is denied.
3. Analyst/viewer cannot modify profiles; owner/admin can.
4. A deterministic mock adapter proves task routing without external network access.
5. Retry and fallback behavior is tested, including first-provider failure and second-provider success.
6. Structured JSON generation is parsed and invalid JSON is rejected.
7. Usage is normalized into the canonical result/trace model.
8. Execution attempts create tenant-scoped trace records without raw prompt/output content.
9. OpenAI and Gemini adapters pass request/response contract tests using mocked HTTP transports.
10. PostgreSQL migration and `alembic check` pass.
11. Ruff, Pyright strict, and pytest pass.
12. Existing Docker/Compose runtime remains healthy.
