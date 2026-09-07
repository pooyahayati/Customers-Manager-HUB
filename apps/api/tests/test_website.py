import pytest

from customers_manager_hub.agent_runtime import channel_instruction
from customers_manager_hub.channel_models import ChannelCapability, ChannelType
from customers_manager_hub.website import WebsiteAdapter, normalize_website_origin


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.COM", "https://example.com"),
        ("https://example.com/", "https://example.com"),
        ("https://example.com:8443", "https://example.com:8443"),
        ("https://example.com:443", "https://example.com"),
        ("https://BÜCHER.example", "https://xn--bcher-kva.example"),
        ("http://localhost:3000", "http://localhost:3000"),
        ("http://localhost:80", "http://localhost"),
        ("http://127.0.0.1", "http://127.0.0.1"),
    ],
)
def test_normalize_website_origin(raw: str, expected: str) -> None:
    assert normalize_website_origin(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "*",
        "null",
        "http://example.com",
        "https://example.com/path",
        "https://example.com/?query=1",
        "https://user@example.com",
        "ftp://example.com",
    ],
)
def test_invalid_website_origin(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_website_origin(raw)


def test_website_adapter_and_agent_instruction_are_channel_specific() -> None:
    adapter = WebsiteAdapter()
    assert adapter.channel_type == ChannelType.WEBSITE
    assert adapter.capabilities == frozenset(
        {ChannelCapability.TEXT, ChannelCapability.OUTBOUND_TEXT}
    )
    instruction = channel_instruction(ChannelType.WEBSITE)
    assert "Website chat" in instruction
    assert "4096" in instruction
