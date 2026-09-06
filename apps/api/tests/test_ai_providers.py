import asyncio
import base64
import json
from typing import cast

import httpx2
import pytest

from customers_manager_hub.ai_gateway import (
    AIProviderError,
    EmbeddingRequest,
    GenerationRequest,
    TranscriptionRequest,
)
from customers_manager_hub.ai_providers import GeminiAdapter, OpenAIAdapter


def json_body(request: httpx2.Request) -> dict[str, object]:
    parsed: object = json.loads(request.content)
    assert isinstance(parsed, dict)
    return cast(dict[str, object], parsed)


def test_openai_structured_generation_contract_and_usage() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/responses"
        assert request.headers["authorization"] == "Bearer test-openai-key"
        body = json_body(request)
        assert body["model"] == "test-openai-model"
        assert body["input"] == "Classify this"
        assert body["store"] is False
        text_config = cast(dict[str, object], body["text"])
        response_format = cast(dict[str, object], text_config["format"])
        assert response_format["type"] == "json_schema"
        assert response_format["strict"] is True
        return httpx2.Response(
            200,
            request=request,
            headers={"x-request-id": "request-header-id"},
            json={
                "id": "resp_123",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"intent":"sales"}'}],
                    }
                ],
                "usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
            },
        )

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            adapter = OpenAIAdapter(client, "test-openai-key")
            result = await adapter.generate(
                "test-openai-model",
                GenerationRequest(
                    input_text="Classify this",
                    json_schema={
                        "type": "object",
                        "properties": {"intent": {"type": "string"}},
                        "required": ["intent"],
                        "additionalProperties": False,
                    },
                    schema_name="intent_result",
                ),
                {"temperature": 0},
                5,
            )
            assert result.provider == "openai"
            assert result.structured == {"intent": "sales"}
            assert result.provider_request_id == "resp_123"
            assert result.usage.total_tokens == 10

    asyncio.run(run())


def test_openai_rejects_invalid_structured_output() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            request=request,
            json={
                "id": "resp_bad",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "not-json"}],
                    }
                ],
            },
        )

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            adapter = OpenAIAdapter(client, "test-openai-key")
            with pytest.raises(AIProviderError, match="invalid_structured_output"):
                await adapter.generate(
                    "test-openai-model",
                    GenerationRequest(
                        input_text="Return JSON",
                        json_schema={"type": "object"},
                    ),
                    {},
                    5,
                )

    asyncio.run(run())


def test_provider_schema_validation_errors_are_normalized() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            request=request,
            json={"data": "not-an-embedding-list"},
        )

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            adapter = OpenAIAdapter(client, "test-openai-key")
            with pytest.raises(AIProviderError, match="openai_invalid_response") as error:
                await adapter.embed(
                    "test-embedding-model",
                    EmbeddingRequest(inputs=("alpha",)),
                    {},
                    5,
                )
            assert error.value.retryable is True

    asyncio.run(run())


def test_openai_embedding_and_transcription_contracts() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["authorization"] == "Bearer test-openai-key"
        if request.url.path == "/v1/embeddings":
            body = json_body(request)
            assert body["input"] == ["alpha", "beta"]
            return httpx2.Response(
                200,
                request=request,
                headers={"x-request-id": "embed-request"},
                json={
                    "data": [
                        {"index": 0, "embedding": [0.1, 0.2]},
                        {"index": 1, "embedding": [0.3, 0.4]},
                    ],
                    "usage": {"input_tokens": 2, "total_tokens": 2},
                },
            )
        assert request.url.path == "/v1/audio/transcriptions"
        assert b"test-audio" in request.content
        return httpx2.Response(
            200,
            request=request,
            headers={"x-request-id": "transcribe-request"},
            json={"text": "hello world"},
        )

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            adapter = OpenAIAdapter(client, "test-openai-key")
            embeddings = await adapter.embed(
                "test-embedding-model",
                EmbeddingRequest(inputs=("alpha", "beta")),
                {},
                5,
            )
            assert embeddings.embeddings == ((0.1, 0.2), (0.3, 0.4))
            transcript = await adapter.transcribe(
                "test-transcription-model",
                TranscriptionRequest(
                    audio=b"test-audio",
                    filename="voice.ogg",
                    mime_type="audio/ogg",
                ),
                {},
                5,
            )
            assert transcript.text == "hello world"

    asyncio.run(run())


def test_gemini_generation_embedding_and_inline_transcription_contracts() -> None:
    encoded_audio = base64.b64encode(b"test-audio").decode("ascii")

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["x-goog-api-key"] == "test-gemini-key"
        body = json_body(request)
        if request.url.path.endswith(":batchEmbedContents"):
            requests = cast(list[object], body["requests"])
            assert len(requests) == 2
            return httpx2.Response(
                200,
                request=request,
                json={
                    "embeddings": [{"values": [0.5, 0.6]}, {"values": [0.7, 0.8]}],
                    "usageMetadata": {"promptTokenCount": 2, "totalTokenCount": 2},
                },
            )

        contents = cast(list[object], body["contents"])
        first_content = cast(dict[str, object], contents[0])
        parts = cast(list[object], first_content["parts"])
        if len(parts) == 2:
            audio_part = cast(dict[str, object], parts[1])
            inline_data = cast(dict[str, object], audio_part["inlineData"])
            assert inline_data["data"] == encoded_audio
            response_text = "transcribed audio"
        else:
            generation_config = cast(dict[str, object], body["generationConfig"])
            assert "responseFormat" in generation_config
            response_text = '{"intent":"support"}'
        return httpx2.Response(
            200,
            request=request,
            headers={"x-request-id": "gemini-request"},
            json={
                "candidates": [{"content": {"parts": [{"text": response_text}]}}],
                "usageMetadata": {
                    "promptTokenCount": 4,
                    "candidatesTokenCount": 2,
                    "totalTokenCount": 6,
                },
            },
        )

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            adapter = GeminiAdapter(client, "test-gemini-key")
            generated = await adapter.generate(
                "test-gemini-model",
                GenerationRequest(
                    input_text="Classify",
                    json_schema={"type": "object"},
                ),
                {"temperature": 0},
                5,
            )
            assert generated.structured == {"intent": "support"}
            assert generated.usage.total_tokens == 6

            embeddings = await adapter.embed(
                "test-gemini-embedding",
                EmbeddingRequest(inputs=("alpha", "beta")),
                {},
                5,
            )
            assert embeddings.embeddings == ((0.5, 0.6), (0.7, 0.8))

            transcript = await adapter.transcribe(
                "test-gemini-transcription",
                TranscriptionRequest(
                    audio=b"test-audio",
                    filename="voice.ogg",
                    mime_type="audio/ogg",
                ),
                {},
                5,
            )
            assert transcript.text == "transcribed audio"

    asyncio.run(run())
