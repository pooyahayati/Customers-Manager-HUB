# ADR-016 — Gemini-First Provider-Neutral Voice Transcription

Status: Accepted

## Context

Customers Manager HUB already has a provider-independent AI Gateway with a dedicated `voice_transcription` task type and provider adapters, while Telegram already normalizes voice messages and persists their external media references.

Milestone 10 must complete the end-to-end voice path without coupling the channel or Agent Runtime to one AI vendor. Product preference is to use Gemini as the primary speech-to-text provider.

Google's current Gemini API provides a dedicated transcription model (`gemini-3.5-transcribe`) through the Interactions API and recommends the Interactions API for new Gemini integrations. Audio files can be uploaded through the Gemini Files API and referenced by URI.

## Decision

1. Voice transcription remains an `AIGateway` operation selected by the tenant `voice_transcription` task profile.
2. Gemini is the recommended first route (`priority=0`) using `gemini-3.5-transcribe`.
3. The Gemini adapter uses the Gemini Files API plus Interactions API for dedicated transcription rather than adding Gemini-specific logic to channel/agent modules.
4. OpenAI remains a supported lower-priority fallback route when configured.
5. A successful transcript is persisted into the original inbound voice `Message.text`; the original `message_type=voice` is preserved.
6. Agent Runtime accepts voice messages only after a persisted transcript exists, then reuses the normal text/RAG/tool response path.
7. Telegram media downloading remains a channel-adapter responsibility with server-side credential use.
8. Provider file objects used only for transcription are deleted best-effort after the call.
9. Provider-specific transcription parameters are allowlisted/validated in the provider adapter instead of forwarding arbitrary JSON.

## Alternatives considered

### Hard-code Gemini directly in the worker

Rejected. It would violate task-based model routing and make fallback/provider substitution difficult.

### Keep Gemini transcription on generic `generateContent` with inline audio

Rejected for M10. It works for general audio understanding, but the dedicated transcription model and Interactions API provide the clearer current speech-to-text contract and avoid treating transcription as generic prompting.

### Add a standalone speech microservice

Rejected. Current load and lifecycle fit the modular monolith + worker architecture; a new service would add deployment and observability overhead without a demonstrated need.

### Convert voice messages into new synthetic text messages

Rejected. It would duplicate the customer's inbound event and complicate idempotency/conversation history. The transcript enriches the original voice message instead.

## Consequences

- Gemini becomes the recommended primary STT route without provider lock-in.
- The worker needs a bounded Telegram media download step before AI execution.
- The Gemini adapter gains resumable Files API upload, Interactions API parsing, and cleanup behavior.
- Voice retries remain safe because persisted transcript text is the idempotency boundary.
- Existing AI execution traces continue to expose selected provider/model and usage/failure information.
- The Agent/RAG/Tool layers remain channel- and provider-independent.

## Follow-up implications

- Live/streaming transcription is not part of M10 and can use Gemini Live or another provider later through a separate runtime contract.
- Retention/deletion policies for raw customer voice are finalized in M15.
- Speaker diarization/timestamps may be surfaced later without changing the core transcription operation.
