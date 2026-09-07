from contextlib import suppress
from typing import cast
from urllib.parse import quote, urlsplit

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from customers_manager_hub.ai_gateway import (
    AIProviderError,
    AIUsage,
    TranscriptionRequest,
    TranscriptionResult,
)

_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
_GEMINI_UPLOAD_BASE = "https://generativelanguage.googleapis.com/upload/v1beta"
_MAX_GEMINI_AUDIO_BYTES = 20 * 1024 * 1024
_MAX_LANGUAGE_CODES = 8
_MAX_CUSTOM_VOCABULARY = 100


class _GeminiUploadedFile(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str
    uri: str
    mime_type: str | None = Field(default=None, alias="mimeType")


class _GeminiUploadEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file: _GeminiUploadedFile


class _GeminiInteractionContent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    text: str | None = None


def _empty_content() -> list[_GeminiInteractionContent]:
    return []


class _GeminiInteractionStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    content: list[_GeminiInteractionContent] = Field(default_factory=_empty_content)


def _empty_steps() -> list[_GeminiInteractionStep]:
    return []


class _GeminiInteractionUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_input_tokens: int | None = None
    total_output_tokens: int | None = None
    total_tokens: int | None = None


class _GeminiInteractionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    status: str | None = None
    steps: list[_GeminiInteractionStep] = Field(default_factory=_empty_steps)
    usage: _GeminiInteractionUsage | None = None


def _retryable_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or status_code >= 500


def _provider_error(status_code: int) -> AIProviderError:
    return AIProviderError(
        f"gemini_http_{status_code}",
        retryable=_retryable_status(status_code),
    )


def _validate_upload_url(value: str | None) -> str:
    if value is None:
        raise AIProviderError("gemini_upload_url_missing", retryable=True)
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname != "generativelanguage.googleapis.com":
        raise AIProviderError("gemini_upload_url_invalid", retryable=True)
    return value


def _validate_uploaded_file(file: _GeminiUploadedFile) -> _GeminiUploadedFile:
    if not file.name.startswith("files/") or "/" in file.name.removeprefix("files/"):
        raise AIProviderError("gemini_file_name_invalid", retryable=True)
    parsed = urlsplit(file.uri)
    if parsed.scheme != "https" or parsed.hostname != "generativelanguage.googleapis.com":
        raise AIProviderError("gemini_file_uri_invalid", retryable=True)
    return file


def _string_list(
    value: object,
    *,
    key: str,
    max_items: int,
    max_length: int,
) -> list[str]:
    if not isinstance(value, list):
        raise AIProviderError(f"gemini_invalid_{key}", retryable=False)
    items: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str):
            raise AIProviderError(f"gemini_invalid_{key}", retryable=False)
        normalized = item.strip()
        if not normalized or len(normalized) > max_length:
            raise AIProviderError(f"gemini_invalid_{key}", retryable=False)
        items.append(normalized)
    if len(items) > max_items:
        raise AIProviderError(f"gemini_invalid_{key}", retryable=False)
    return items


def _transcription_config(parameters: dict[str, object]) -> dict[str, object]:
    unknown = set(parameters) - {"language_codes", "mode", "custom_vocabulary"}
    if unknown:
        raise AIProviderError("gemini_invalid_transcription_parameter", retryable=False)

    config: dict[str, object] = {}
    if "language_codes" in parameters:
        raw = parameters["language_codes"]
        if raw == []:
            config["language_codes"] = []
        else:
            config["language_codes"] = _string_list(
                raw,
                key="language_codes",
                max_items=_MAX_LANGUAGE_CODES,
                max_length=35,
            )
    if "custom_vocabulary" in parameters:
        config["custom_vocabulary"] = _string_list(
            parameters["custom_vocabulary"],
            key="custom_vocabulary",
            max_items=_MAX_CUSTOM_VOCABULARY,
            max_length=100,
        )
    mode = parameters.get("mode", "verbatim")
    if not isinstance(mode, str) or mode not in {"verbatim", "smart"}:
        raise AIProviderError("gemini_invalid_transcription_mode", retryable=False)
    config["mode"] = "smart" if mode == "smart" else {"type": "verbatim"}
    return config


def _interaction_text(response: _GeminiInteractionResponse) -> str:
    parts = [
        item.text
        for step in response.steps
        if step.type == "model_output"
        for item in step.content
        if item.type == "text" and item.text is not None
    ]
    text = "".join(parts).strip()
    if not text:
        raise AIProviderError("gemini_empty_output", retryable=True)
    return text


def _interaction_usage(value: _GeminiInteractionUsage | None) -> AIUsage:
    if value is None:
        return AIUsage()
    return AIUsage(
        input_tokens=value.total_input_tokens,
        output_tokens=value.total_output_tokens,
        total_tokens=value.total_tokens,
    )


async def _delete_file_best_effort(
    client: httpx2.AsyncClient,
    api_key: str,
    name: str,
    timeout_seconds: int,
) -> None:
    with suppress(httpx2.HTTPError):
        await client.delete(
            f"{_GEMINI_API_BASE}/{quote(name, safe='/._-')}",
            headers={"x-goog-api-key": api_key},
            timeout=float(timeout_seconds),
        )


async def _upload_audio(
    client: httpx2.AsyncClient,
    api_key: str,
    request: TranscriptionRequest,
    timeout_seconds: int,
) -> _GeminiUploadedFile:
    headers = {
        "x-goog-api-key": api_key,
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(len(request.audio)),
        "X-Goog-Upload-Header-Content-Type": request.mime_type,
        "Content-Type": "application/json",
    }
    try:
        start = await client.post(
            f"{_GEMINI_UPLOAD_BASE}/files",
            headers=headers,
            json={"file": {"display_name": request.filename[:512]}},
            timeout=float(timeout_seconds),
        )
    except httpx2.TimeoutException as exc:
        raise AIProviderError("gemini_timeout", retryable=True) from exc
    except httpx2.TransportError as exc:
        raise AIProviderError("gemini_transport_error", retryable=True) from exc
    if start.status_code >= 400:
        raise _provider_error(start.status_code)
    upload_url = _validate_upload_url(start.headers.get("x-goog-upload-url"))

    try:
        uploaded = await client.post(
            upload_url,
            headers={
                "Content-Length": str(len(request.audio)),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
            },
            content=request.audio,
            timeout=float(timeout_seconds),
        )
    except httpx2.TimeoutException as exc:
        raise AIProviderError("gemini_timeout", retryable=True) from exc
    except httpx2.TransportError as exc:
        raise AIProviderError("gemini_transport_error", retryable=True) from exc
    if uploaded.status_code >= 400:
        raise _provider_error(uploaded.status_code)
    try:
        payload: object = uploaded.json()
        envelope = _GeminiUploadEnvelope.model_validate(payload)
    except (ValueError, ValidationError) as exc:
        raise AIProviderError("gemini_invalid_response", retryable=True) from exc
    return _validate_uploaded_file(envelope.file)


async def transcribe_with_gemini(
    client: httpx2.AsyncClient,
    api_key: str | None,
    model_id: str,
    request: TranscriptionRequest,
    parameters: dict[str, object],
    timeout_seconds: int,
) -> TranscriptionResult:
    if not api_key:
        raise AIProviderError("gemini_not_configured", retryable=False)
    if not request.audio:
        raise AIProviderError("gemini_empty_audio", retryable=False)
    if len(request.audio) > _MAX_GEMINI_AUDIO_BYTES:
        raise AIProviderError("gemini_audio_too_large", retryable=False)
    if request.prompt is not None and request.prompt.strip():
        raise AIProviderError("gemini_transcription_prompt_unsupported", retryable=False)

    transcription_config = _transcription_config(parameters)
    uploaded = await _upload_audio(client, api_key, request, timeout_seconds)
    try:
        payload: dict[str, object] = {
            "model": model_id,
            "input": [
                {
                    "type": "audio",
                    "uri": uploaded.uri,
                    "mime_type": request.mime_type,
                }
            ],
            "generation_config": {"transcription_config": transcription_config},
        }
        try:
            response = await client.post(
                f"{_GEMINI_API_BASE}/interactions",
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=float(timeout_seconds),
            )
        except httpx2.TimeoutException as exc:
            raise AIProviderError("gemini_timeout", retryable=True) from exc
        except httpx2.TransportError as exc:
            raise AIProviderError("gemini_transport_error", retryable=True) from exc
        if response.status_code >= 400:
            raise _provider_error(response.status_code)
        try:
            raw_payload: object = response.json()
            parsed = _GeminiInteractionResponse.model_validate(raw_payload)
        except (ValueError, ValidationError) as exc:
            raise AIProviderError("gemini_invalid_response", retryable=True) from exc
        if parsed.status is not None and parsed.status not in {"completed", "succeeded"}:
            raise AIProviderError("gemini_transcription_incomplete", retryable=True)
        return TranscriptionResult(
            provider="gemini",
            model_id=model_id,
            text=_interaction_text(parsed),
            usage=_interaction_usage(parsed.usage),
            provider_request_id=parsed.id or response.headers.get("x-request-id"),
        )
    finally:
        await _delete_file_best_effort(client, api_key, uploaded.name, timeout_seconds)
