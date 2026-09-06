import base64
import json
from typing import cast
from urllib.parse import quote

import httpx2
from pydantic import BaseModel, ConfigDict, Field

from customers_manager_hub.ai_gateway import (
    AIOperation,
    AIProviderError,
    AIProviderRegistry,
    AIUsage,
    EmbeddingRequest,
    EmbeddingResult,
    GenerationRequest,
    GenerationResult,
    TranscriptionRequest,
    TranscriptionResult,
)
from customers_manager_hub.config import Settings

OPENAI_BASE_URL = "https://api.openai.com/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_INLINE_AUDIO_LIMIT_BYTES = 20 * 1024 * 1024
LIVE_AI_PROVIDER_KEYS = frozenset({"openai", "gemini"})


class _OpenAIContent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    text: str | None = None


class _OpenAIOutputItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    content: list[_OpenAIContent] = Field(default_factory=list)


class _OpenAIUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class _OpenAIResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    output: list[_OpenAIOutputItem] = Field(default_factory=list)
    usage: _OpenAIUsage | None = None


class _OpenAIEmbeddingItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int
    embedding: list[float]


class _OpenAIEmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[_OpenAIEmbeddingItem]
    usage: _OpenAIUsage | None = None


class _OpenAITranscriptionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    usage: _OpenAIUsage | None = None


class _GeminiPart(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str | None = None


class _GeminiContent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    parts: list[_GeminiPart] = Field(default_factory=list)


class _GeminiCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: _GeminiContent | None = None


class _GeminiUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_token_count: int | None = Field(default=None, alias="promptTokenCount")
    candidates_token_count: int | None = Field(default=None, alias="candidatesTokenCount")
    total_token_count: int | None = Field(default=None, alias="totalTokenCount")


class _GeminiResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    candidates: list[_GeminiCandidate] = Field(default_factory=list)
    usage_metadata: _GeminiUsage | None = Field(default=None, alias="usageMetadata")


class _GeminiEmbedding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    values: list[float]


class _GeminiEmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    embeddings: list[_GeminiEmbedding]
    usage_metadata: _GeminiUsage | None = Field(default=None, alias="usageMetadata")


def _usage_from_openai(value: _OpenAIUsage | None) -> AIUsage:
    if value is None:
        return AIUsage()
    return AIUsage(
        input_tokens=value.input_tokens,
        output_tokens=value.output_tokens,
        total_tokens=value.total_tokens,
    )


def _usage_from_gemini(value: _GeminiUsage | None) -> AIUsage:
    if value is None:
        return AIUsage()
    return AIUsage(
        input_tokens=value.prompt_token_count,
        output_tokens=value.candidates_token_count,
        total_tokens=value.total_token_count,
    )


def _parse_structured_json(text: str) -> dict[str, object]:
    try:
        parsed: object = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise AIProviderError("invalid_structured_output", retryable=True) from exc
    if not isinstance(parsed, dict):
        raise AIProviderError("invalid_structured_output", retryable=True)
    mapping = cast(dict[object, object], parsed)
    result: dict[str, object] = {}
    for key, value in mapping.items():
        if not isinstance(key, str):
            raise AIProviderError("invalid_structured_output", retryable=True)
        result[key] = value
    return result


def _is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or status_code >= 500


async def _post_json(
    client: httpx2.AsyncClient,
    *,
    provider: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, object],
    timeout_seconds: int,
) -> tuple[httpx2.Response, object]:
    try:
        response = await client.post(
            url,
            headers=headers,
            json=payload,
            timeout=float(timeout_seconds),
        )
    except httpx2.TimeoutException as exc:
        raise AIProviderError(f"{provider}_timeout", retryable=True) from exc
    except httpx2.TransportError as exc:
        raise AIProviderError(f"{provider}_transport_error", retryable=True) from exc

    if response.status_code >= 400:
        raise AIProviderError(
            f"{provider}_http_{response.status_code}",
            retryable=_is_retryable_status(response.status_code),
        )
    try:
        payload_object: object = response.json()
    except ValueError as exc:
        raise AIProviderError(f"{provider}_invalid_response", retryable=True) from exc
    return response, payload_object


def _openai_text(response: _OpenAIResponse) -> str:
    parts = [
        content.text
        for item in response.output
        if item.type == "message"
        for content in item.content
        if content.type == "output_text" and content.text is not None
    ]
    text = "".join(parts).strip()
    if not text:
        raise AIProviderError("openai_empty_output", retryable=True)
    return text


def _gemini_text(response: _GeminiResponse) -> str:
    parts = [
        part.text
        for candidate in response.candidates
        if candidate.content is not None
        for part in candidate.content.parts
        if part.text is not None
    ]
    text = "".join(parts).strip()
    if not text:
        raise AIProviderError("gemini_empty_output", retryable=True)
    return text


def _model_path(model_id: str) -> str:
    return quote(model_id, safe="-._")


class OpenAIAdapter:
    def __init__(self, client: httpx2.AsyncClient, api_key: str | None) -> None:
        self._client = client
        self._api_key = api_key

    @property
    def key(self) -> str:
        return "openai"

    def supports(self, operation: AIOperation) -> bool:
        return operation in {
            AIOperation.GENERATION,
            AIOperation.EMBEDDING,
            AIOperation.TRANSCRIPTION,
        }

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise AIProviderError("openai_not_configured", retryable=False)
        return {"Authorization": f"Bearer {self._api_key}"}

    async def generate(
        self,
        model_id: str,
        request: GenerationRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> GenerationResult:
        payload = dict(parameters)
        payload.update(
            {
                "model": model_id,
                "input": request.input_text,
                "store": False,
            }
        )
        if request.instructions is not None:
            payload["instructions"] = request.instructions
        if request.json_schema is not None:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": request.schema_name,
                    "schema": request.json_schema,
                    "strict": True,
                }
            }

        raw_response, raw_payload = await _post_json(
            self._client,
            provider=self.key,
            url=f"{OPENAI_BASE_URL}/responses",
            headers=self._headers(),
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        parsed = _OpenAIResponse.model_validate(raw_payload)
        text = _openai_text(parsed)
        structured = _parse_structured_json(text) if request.json_schema is not None else None
        request_id = parsed.id or raw_response.headers.get("x-request-id")
        return GenerationResult(
            provider=self.key,
            model_id=model_id,
            text=text,
            structured=structured,
            usage=_usage_from_openai(parsed.usage),
            provider_request_id=request_id,
        )

    async def embed(
        self,
        model_id: str,
        request: EmbeddingRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> EmbeddingResult:
        payload = dict(parameters)
        payload.update({"model": model_id, "input": list(request.inputs)})
        raw_response, raw_payload = await _post_json(
            self._client,
            provider=self.key,
            url=f"{OPENAI_BASE_URL}/embeddings",
            headers=self._headers(),
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        parsed = _OpenAIEmbeddingResponse.model_validate(raw_payload)
        ordered = sorted(parsed.data, key=lambda item: item.index)
        if len(ordered) != len(request.inputs):
            raise AIProviderError("openai_embedding_count_mismatch", retryable=True)
        return EmbeddingResult(
            provider=self.key,
            model_id=model_id,
            embeddings=tuple(tuple(item.embedding) for item in ordered),
            usage=_usage_from_openai(parsed.usage),
            provider_request_id=raw_response.headers.get("x-request-id"),
        )

    async def transcribe(
        self,
        model_id: str,
        request: TranscriptionRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> TranscriptionResult:
        data: dict[str, str] = {"model": model_id}
        for key, value in parameters.items():
            if isinstance(value, bool):
                data[key] = "true" if value else "false"
            elif isinstance(value, (str, int, float)):
                data[key] = str(value)
            else:
                raise AIProviderError("openai_invalid_transcription_parameter", retryable=False)
        if request.prompt is not None:
            data["prompt"] = request.prompt
        try:
            response = await self._client.post(
                f"{OPENAI_BASE_URL}/audio/transcriptions",
                headers=self._headers(),
                data=data,
                files={"file": (request.filename, request.audio, request.mime_type)},
                timeout=float(timeout_seconds),
            )
        except httpx2.TimeoutException as exc:
            raise AIProviderError("openai_timeout", retryable=True) from exc
        except httpx2.TransportError as exc:
            raise AIProviderError("openai_transport_error", retryable=True) from exc
        if response.status_code >= 400:
            raise AIProviderError(
                f"openai_http_{response.status_code}",
                retryable=_is_retryable_status(response.status_code),
            )
        try:
            raw_payload: object = response.json()
        except ValueError as exc:
            raise AIProviderError("openai_invalid_response", retryable=True) from exc
        parsed = _OpenAITranscriptionResponse.model_validate(raw_payload)
        return TranscriptionResult(
            provider=self.key,
            model_id=model_id,
            text=parsed.text.strip(),
            usage=_usage_from_openai(parsed.usage),
            provider_request_id=response.headers.get("x-request-id"),
        )


class GeminiAdapter:
    def __init__(self, client: httpx2.AsyncClient, api_key: str | None) -> None:
        self._client = client
        self._api_key = api_key

    @property
    def key(self) -> str:
        return "gemini"

    def supports(self, operation: AIOperation) -> bool:
        return operation in {
            AIOperation.GENERATION,
            AIOperation.EMBEDDING,
            AIOperation.TRANSCRIPTION,
        }

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise AIProviderError("gemini_not_configured", retryable=False)
        return {"x-goog-api-key": self._api_key}

    async def generate(
        self,
        model_id: str,
        request: GenerationRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> GenerationResult:
        generation_config = dict(parameters)
        if request.json_schema is not None:
            generation_config["responseFormat"] = {
                "text": {
                    "mimeType": "application/json",
                    "schema": request.json_schema,
                }
            }
        payload: dict[str, object] = {
            "contents": [{"role": "user", "parts": [{"text": request.input_text}]}],
        }
        if generation_config:
            payload["generationConfig"] = generation_config
        if request.instructions is not None:
            payload["systemInstruction"] = {"parts": [{"text": request.instructions}]}

        raw_response, raw_payload = await _post_json(
            self._client,
            provider=self.key,
            url=f"{GEMINI_BASE_URL}/models/{_model_path(model_id)}:generateContent",
            headers=self._headers(),
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        parsed = _GeminiResponse.model_validate(raw_payload)
        text = _gemini_text(parsed)
        structured = _parse_structured_json(text) if request.json_schema is not None else None
        return GenerationResult(
            provider=self.key,
            model_id=model_id,
            text=text,
            structured=structured,
            usage=_usage_from_gemini(parsed.usage_metadata),
            provider_request_id=raw_response.headers.get("x-request-id"),
        )

    async def embed(
        self,
        model_id: str,
        request: EmbeddingRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> EmbeddingResult:
        model_name = f"models/{model_id}"
        requests: list[dict[str, object]] = []
        for text in request.inputs:
            item: dict[str, object] = {
                "model": model_name,
                "content": {"parts": [{"text": text}]},
            }
            if parameters:
                item["config"] = dict(parameters)
            requests.append(item)
        raw_response, raw_payload = await _post_json(
            self._client,
            provider=self.key,
            url=f"{GEMINI_BASE_URL}/models/{_model_path(model_id)}:batchEmbedContents",
            headers=self._headers(),
            payload={"requests": requests},
            timeout_seconds=timeout_seconds,
        )
        parsed = _GeminiEmbeddingResponse.model_validate(raw_payload)
        if len(parsed.embeddings) != len(request.inputs):
            raise AIProviderError("gemini_embedding_count_mismatch", retryable=True)
        return EmbeddingResult(
            provider=self.key,
            model_id=model_id,
            embeddings=tuple(tuple(item.values) for item in parsed.embeddings),
            usage=_usage_from_gemini(parsed.usage_metadata),
            provider_request_id=raw_response.headers.get("x-request-id"),
        )

    async def transcribe(
        self,
        model_id: str,
        request: TranscriptionRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> TranscriptionResult:
        if len(request.audio) > GEMINI_INLINE_AUDIO_LIMIT_BYTES:
            raise AIProviderError("gemini_inline_audio_too_large", retryable=False)
        prompt = request.prompt or "Transcribe this audio accurately. Return only the transcript."
        payload: dict[str, object] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inlineData": {
                                "mimeType": request.mime_type,
                                "data": base64.b64encode(request.audio).decode("ascii"),
                            }
                        },
                    ],
                }
            ]
        }
        if parameters:
            payload["generationConfig"] = dict(parameters)
        raw_response, raw_payload = await _post_json(
            self._client,
            provider=self.key,
            url=f"{GEMINI_BASE_URL}/models/{_model_path(model_id)}:generateContent",
            headers=self._headers(),
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        parsed = _GeminiResponse.model_validate(raw_payload)
        return TranscriptionResult(
            provider=self.key,
            model_id=model_id,
            text=_gemini_text(parsed),
            usage=_usage_from_gemini(parsed.usage_metadata),
            provider_request_id=raw_response.headers.get("x-request-id"),
        )


def build_live_provider_registry(
    settings: Settings,
    client: httpx2.AsyncClient,
) -> AIProviderRegistry:
    openai_key = (
        settings.openai_api_key.get_secret_value() if settings.openai_api_key is not None else None
    )
    gemini_key = (
        settings.google_gemini_api_key.get_secret_value()
        if settings.google_gemini_api_key is not None
        else None
    )
    return AIProviderRegistry(
        [
            OpenAIAdapter(client, openai_key),
            GeminiAdapter(client, gemini_key),
        ]
    )
