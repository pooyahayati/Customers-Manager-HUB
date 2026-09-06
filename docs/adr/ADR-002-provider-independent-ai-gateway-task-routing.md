# ADR-002: Provider-Independent AI Gateway and Task Routing

**Status:** Accepted

## Context

The platform must support OpenAI, Gemini, and future AI providers. Different AI tasks may require different providers/models for quality, capability, cost, latency, or availability reasons.

Direct provider SDK usage inside business modules would create lock-in and make routing/fallback logic difficult to maintain.

## Decision

Introduce an internal AI Gateway with provider-neutral operation contracts and provider adapters.

Business/application modules call canonical AI operations instead of provider SDKs.

AI provider/model selection is resolved by task profiles such as:

- customer response
- voice transcription
- intent classification
- conversation summary
- customer memory extraction
- embeddings
- image understanding
- reranking
- complex reasoning/tool use

A task profile may include provider, model identifier, parameters, timeout, retry policy, and fallback chain.

Exact provider model IDs are configuration/registry data.

## Alternatives Considered

### One global model configuration

Rejected because transcription, classification, embeddings, and customer responses have different requirements and cost profiles.

### Direct OpenAI/Gemini SDK calls from features

Rejected because it couples domain logic to external provider contracts and duplicates fallback/usage/error handling.

### Agent framework as the canonical abstraction

Not selected as a core architecture dependency. Frameworks may be used behind internal interfaces only if justified by a later ADR.

## Consequences

Positive:

- Provider portability.
- Task-specific quality/cost control.
- Centralized retry/fallback/usage handling.
- Easier testing with mock providers.

Trade-offs:

- Canonical contracts must account for capability differences between providers.
- Provider-specific advanced features may require explicit extension points.

## Follow-up

- Define canonical AI request/result schemas.
- Define provider capability discovery/validation.
- Define task profile persistence and tenant override rules.
- Add deterministic mock provider for automated tests.
