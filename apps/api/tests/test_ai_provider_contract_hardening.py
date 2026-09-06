import asyncio
import json
from typing import cast

import httpx2
import pytest

from customers_manager_hub.ai_gateway import AIProviderError, EmbeddingRequest, TranscriptionRequest
from customers_manager_hub.ai_providers import GeminiAdapter, OpenAIAdapter


def _json_body(request: httpx2.Request) -> dict[str, object]:
    parsed: object = json.loads(request.content)
    assert isinstance(parsed, dict)
    return cast(dict[str, object], parsed)


def test_gemini_embedding_uses_embed_content_config() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = _json_body(request)
        requests = cast(list[object], body["requests"])
        first = cast(dict[str, object], requests[0])
        assert first["embedContentConfig"] == {"outputDimensionality": 128}
        assert "config" not in first
        return httpx2.Response(
            200,
            request=request,
            json={"embeddings": [{"values": [0.1, 0.2]}]},
        )

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            adapter = GeminiAdapter(client, "test-gemini-key")
            result = await adapter.embed(
                "test-gemini-embedding",
                EmbeddingRequest(inputs=("alpha",)),
                {"outputDimensionality": 128},
                5,
            )
            assert result.embeddings == ((0.1, 0.2),)

    asyncio.run(run())


def test_openai_transcription_rejects_model_override_parameter() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        raise AssertionError("HTTP request must not be sent for a reserved model override")

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            adapter = OpenAIAdapter(client, "test-openai-key")
            with pytest.raises(AIProviderError, match="openai_reserved_transcription_parameter"):
                await adapter.transcribe(
                    "configured-model",
                    TranscriptionRequest(
                        audio=b"audio",
                        filename="voice.ogg",
                        mime_type="audio/ogg",
                    ),
                    {"model": "override-model"},
                    5,
                )

    asyncio.run(run())
