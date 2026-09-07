# Milestone 10 — Voice Transcription End-to-End

Status: Implementation specification

## Goal

Turn supported inbound Telegram voice messages into canonical text using the tenant's `voice_transcription` AI task profile, persist the transcript, and continue through the existing Agent/RAG/Tool response pipeline.

## Product priority

Gemini is the preferred transcription provider for this milestone. The recommended first route is:

- provider: `gemini`
- model: `gemini-3.5-transcribe`
- priority: `0`

The AI Gateway remains provider-independent. Tenants may configure OpenAI or later providers as lower-priority fallback routes.

## Scope

- Telegram voice media download after `getFile` resolution.
- Bounded audio validation and media-type normalization.
- Durable transcription state attached to the existing message/attachment lifecycle.
- `AIGateway.transcribe()` execution through the configured `voice_transcription` profile.
- Gemini dedicated transcription through the Gemini Files API + Interactions API.
- OpenAI transcription fallback through the existing adapter.
- Transcript persistence into the canonical inbound `Message.text` while keeping `Message.message_type=voice`.
- Transcription provider/model/result metadata without storing provider credentials.
- Retry-safe worker orchestration.
- Continue the normal Agent Runtime after a successful transcript.
- Static, integration, provider-contract and Docker regression tests.

## Out of scope

- Live microphone/streaming transcription.
- Phone-call agents.
- Generic audio-file uploads outside supported channel ingestion.
- Speaker diarization UI.
- Word-level timestamp UI.
- Voice synthesis / text-to-speech.
- Long-term raw voice retention policy beyond the existing channel/storage lifecycle; production retention is M15.

## Core invariants

1. Telegram credentials and AI provider credentials never enter model input or transcript text.
2. A voice message remains a canonical `voice` message; its transcript is stored in `Message.text`.
3. The same inbound voice message must not be transcribed twice after a successful persisted transcript.
4. A transcript is never treated as a new external message; it enriches the original canonical message.
5. Agent execution begins only after voice transcription succeeds.
6. Retryable Telegram/AI failures leave the channel job retryable.
7. Terminal validation failures do not loop forever.
8. Provider/model selection happens only through the tenant `voice_transcription` task profile.
9. Gemini is preferred by route priority, not hard-coded inside Agent Runtime.
10. Cross-tenant media/message IDs cannot be used to transcribe another tenant's content.

## Gemini transcription contract

M10 upgrades the Gemini transcription adapter to the current dedicated transcription path:

1. Start a resumable Gemini Files API upload for the bounded audio bytes.
2. Upload/finalize the audio bytes.
3. Call `POST /v1beta/interactions` with the configured model, normally `gemini-3.5-transcribe`.
4. Pass the uploaded file URI as an audio input with its normalized MIME type.
5. Default to automatic language detection and verbatim transcription unless route parameters explicitly choose a supported transcription option.
6. Parse the final text from interaction model-output steps.
7. Delete the temporary Gemini file best-effort after completion/failure.

Supported Gemini transcription route parameters in M10 are allowlisted rather than forwarded blindly:

- `language_codes`: list of BCP-47 language codes, empty/omitted means auto-detect.
- `mode`: `verbatim` or `smart`.
- `custom_vocabulary`: bounded string list.

Diarization/timestamps remain compatible with the provider but are not required for the MVP response pipeline.

## Voice media lifecycle

Telegram ingestion already persists the original voice attachment and external media ID. Channel processing resolves Telegram `file_path` and stable unique ID.

M10 then:

1. Loads the same-tenant inbound voice message and attachment.
2. Resolves the encrypted Telegram bot credential server-side.
3. Downloads bytes from the Telegram file endpoint using the provider adapter.
4. Rejects empty/oversized/unsupported audio.
5. Sends bytes to `AIGateway.transcribe()`.
6. Stores the normalized transcript in `Message.text`.
7. Stores non-secret transcription metadata on the attachment (`provider`, `model_id`, provider request id where present).
8. Agent Runtime consumes the now-textual voice message through the same conversation/RAG/tool logic used for text messages.

## Limits

- Maximum voice bytes accepted for transcription: 20 MiB.
- Empty media is rejected.
- Supported MIME types initially include Telegram voice defaults and Gemini/OpenAI-compatible common audio types: OGG/Opus, MPEG/MP3, WAV, M4A, AAC, FLAC and WebM.
- Transcript must be non-blank and contain no NUL characters.
- Transcript is bounded before it enters conversation context.

## Retry and idempotency

- Successful transcript persistence is the idempotency boundary: if a voice message already has non-blank `Message.text`, transcription returns without another provider call.
- Telegram download 408/409/425/429/5xx and transport/timeouts are retryable.
- AI Gateway retry/fallback behavior remains authoritative for provider failures.
- Invalid/empty media and invalid transcript responses are terminal.
- A failed attempt never overwrites a previously persisted successful transcript.

## Agent Runtime integration

`_resolve_or_create_run()` accepts:

- normal inbound text messages with text;
- inbound voice messages only when a non-blank persisted transcript exists.

Conversation context labels the current input as the customer message text; the source `MessageType.VOICE` remains available in persistence/audit layers.

RAG and Tool Runtime require no duplicate voice-specific business logic.

## Configuration

A recommended tenant task profile is:

```json
{
  "timeout_seconds": 60,
  "attempts_per_route": 1,
  "routes": [
    {
      "provider": "gemini",
      "model_id": "gemini-3.5-transcribe",
      "parameters": {"language_codes": [], "mode": "verbatim"}
    },
    {
      "provider": "openai",
      "model_id": "gpt-4o-mini-transcribe",
      "parameters": {}
    }
  ]
}
```

The route order expresses Gemini priority. There is no global provider switch in business code.

## Tests

Required coverage:

- Gemini Files API resumable upload contract.
- Gemini Interactions transcription request/response parsing.
- Gemini route parameter allowlist and invalid combinations.
- Temporary Gemini file cleanup.
- Telegram file download URL/identity/media-size checks.
- Empty/oversized/unsupported media negatives.
- Voice transcript persisted once and provider call not repeated on retry.
- Gemini-first route then OpenAI fallback when Gemini has a retryable failure.
- Cross-tenant negative paths.
- AI execution trace records transcription provider/model without secrets.
- Telegram voice → transcription → Agent/RAG/Tool-compatible response E2E.
- Existing text Telegram/Website regressions.
- Migration/Alembic check if schema changes are required.
- Docker/Compose and non-root regressions.

## Acceptance criteria

M10 is complete when:

1. A Telegram voice message can enter the existing canonical message model.
2. The worker downloads its media without exposing the Telegram token.
3. The tenant's `voice_transcription` profile is resolved with Gemini as the preferred configured route.
4. Gemini `gemini-3.5-transcribe` can produce a persisted transcript through the dedicated API contract.
5. Retryable Gemini failure can fall back to the next configured route.
6. The transcript enriches the original voice message exactly once.
7. The normal Agent Runtime answers the transcribed customer message through the originating Telegram conversation.
8. No duplicate AI business logic or provider-specific logic is added to Agent Runtime.
9. Static, unit, integration and Docker gates pass.
