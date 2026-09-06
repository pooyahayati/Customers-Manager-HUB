import asyncio
import json
from uuid import uuid4

import httpx2
import pytest

from customers_manager_hub.channel_gateway import ChannelProviderError
from customers_manager_hub.models import MessageType
from customers_manager_hub.telegram import TelegramAdapter, normalize_telegram_update


def test_normalize_telegram_text_and_voice_updates() -> None:
    account_id = uuid4()
    text = normalize_telegram_update(
        {
            "update_id": 101,
            "message": {
                "message_id": 7,
                "date": 1_700_000_000,
                "chat": {"id": 42, "type": "private"},
                "from": {
                    "id": 42,
                    "is_bot": False,
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "username": "ada",
                },
                "text": "Hello",
            },
        },
        account_id,
    )
    assert text.message is not None
    assert text.message.external_event_id == "101"
    assert text.message.external_thread_id == "42"
    assert text.message.sender_namespace == "telegram:user"
    assert text.message.sender_external_id == "42"
    assert text.message.sender_display_name == "Ada Lovelace"
    assert text.message.message_type == MessageType.TEXT
    assert text.message.text == "Hello"
    assert text.message.attachments == ()

    voice = normalize_telegram_update(
        {
            "update_id": 102,
            "message": {
                "message_id": 8,
                "date": 1_700_000_001,
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "Ada"},
                "voice": {
                    "file_id": "file-id",
                    "file_unique_id": "unique-id",
                    "duration": 5,
                    "mime_type": "audio/ogg",
                    "file_size": 1234,
                },
            },
        },
        account_id,
    )
    assert voice.message is not None
    assert voice.message.message_type == MessageType.VOICE
    assert len(voice.message.attachments) == 1
    attachment = voice.message.attachments[0]
    assert attachment.external_media_id == "file-id"
    assert attachment.mime_type == "audio/ogg"
    assert attachment.size_bytes == 1234
    assert attachment.metadata == {
        "telegram_file_unique_id": "unique-id",
        "duration_seconds": 5,
    }


def test_normalize_telegram_ignores_unsupported_update_and_group_chat() -> None:
    account_id = uuid4()
    unsupported = normalize_telegram_update({"update_id": 1, "callback_query": {}}, account_id)
    assert unsupported.message is None
    assert unsupported.ignored_reason == "unsupported_update_type"

    group = normalize_telegram_update(
        {
            "update_id": 2,
            "message": {
                "message_id": 1,
                "date": 1_700_000_000,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 42, "is_bot": False, "first_name": "Ada"},
                "text": "ignored",
            },
        },
        account_id,
    )
    assert group.message is None
    assert group.ignored_reason == "unsupported_chat_or_sender"


def test_telegram_adapter_contracts() -> None:
    seen: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content.decode()) if request.content else {}
        seen.append((method, payload))
        if method == "getMe":
            return httpx2.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "id": 999,
                        "is_bot": True,
                        "first_name": "CMH",
                        "username": "cmh_bot",
                    },
                },
            )
        if method == "setWebhook":
            return httpx2.Response(200, json={"ok": True, "result": True})
        if method == "getFile":
            return httpx2.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "file_id": "file-id",
                        "file_unique_id": "unique-id",
                        "file_size": 555,
                        "file_path": "voice/file_1.oga",
                    },
                },
            )
        if method == "sendMessage":
            return httpx2.Response(
                200,
                json={"ok": True, "result": {"message_id": 77, "date": 1_700_000_100}},
            )
        return httpx2.Response(404)

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            adapter = TelegramAdapter(client)
            identity = await adapter.validate_account("123:token")
            assert identity.external_account_id == "999"
            assert identity.username == "cmh_bot"

            await adapter.register_webhook(
                "123:token",
                webhook_url="https://example.com/api/v1/webhooks/telegram/account",
                webhook_secret="secret_value-1",
            )
            media = await adapter.resolve_media("123:token", "file-id")
            assert media.file_path == "voice/file_1.oga"
            assert media.external_unique_id == "unique-id"

            sent = await adapter.send_text(
                "123:token",
                external_thread_id="42",
                text=" hello ",
            )
            assert sent.external_message_id == "77"

    asyncio.run(run())

    assert seen[0] == ("getMe", {})
    assert seen[1] == (
        "setWebhook",
        {
            "url": "https://example.com/api/v1/webhooks/telegram/account",
            "secret_token": "secret_value-1",
            "allowed_updates": ["message"],
            "drop_pending_updates": False,
        },
    )
    assert seen[2] == ("getFile", {"file_id": "file-id"})
    assert seen[3] == ("sendMessage", {"chat_id": "42", "text": "hello"})


def test_telegram_adapter_normalizes_malformed_response_and_api_error() -> None:
    responses = iter(
        [
            httpx2.Response(200, content=b"not-json"),
            httpx2.Response(200, json={"ok": False, "error_code": 429}),
        ]
    )

    def handler(_: httpx2.Request) -> httpx2.Response:
        return next(responses)

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            adapter = TelegramAdapter(client)
            with pytest.raises(ChannelProviderError, match="telegram_invalid_response"):
                await adapter.validate_account("123:token")
            with pytest.raises(ChannelProviderError) as raised:
                await adapter.validate_account("123:token")
            assert raised.value.code == "telegram_api_429"
            assert raised.value.retryable is True

    asyncio.run(run())
