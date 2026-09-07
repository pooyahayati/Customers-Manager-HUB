import asyncio

import httpx2
import pytest

from customers_manager_hub.voice_runtime import (
    MAX_VOICE_AUDIO_BYTES,
    TelegramVoiceMediaDownloader,
    VoiceTranscriptionError,
)


def test_telegram_voice_downloader_contract_and_mime_normalization() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/file/bot900001:test-token/voice/file_1.oga"
        return httpx2.Response(
            200,
            request=request,
            headers={"content-length": "11"},
            content=b"voice-bytes",
        )

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            downloader = TelegramVoiceMediaDownloader(client)
            media = await downloader.download(
                "900001:test-token",
                file_path="voice/file_1.oga",
                mime_type="audio/ogg; codecs=opus",
                size_bytes=11,
            )
            assert media.data == b"voice-bytes"
            assert media.filename == "file_1.oga"
            assert media.mime_type == "audio/ogg"

    asyncio.run(run())


def test_telegram_voice_downloader_rejects_unsafe_or_oversized_media() -> None:
    async def run() -> None:
        async with httpx2.AsyncClient() as client:
            downloader = TelegramVoiceMediaDownloader(client)
            with pytest.raises(VoiceTranscriptionError, match="telegram_voice_file_path_invalid"):
                await downloader.download(
                    "900001:test-token",
                    file_path="../secret",
                    mime_type="audio/ogg",
                    size_bytes=10,
                )
            with pytest.raises(VoiceTranscriptionError, match="voice_media_too_large"):
                await downloader.download(
                    "900001:test-token",
                    file_path="voice/file.oga",
                    mime_type="audio/ogg",
                    size_bytes=MAX_VOICE_AUDIO_BYTES + 1,
                )

    asyncio.run(run())


def test_telegram_voice_download_retryability() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503, request=request)

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            downloader = TelegramVoiceMediaDownloader(client)
            with pytest.raises(
                VoiceTranscriptionError, match="telegram_voice_download_http_503"
            ) as error:
                await downloader.download(
                    "900001:test-token",
                    file_path="voice/file.oga",
                    mime_type="audio/ogg",
                    size_bytes=10,
                )
            assert error.value.retryable is True

    asyncio.run(run())
