from urllib.parse import urlsplit

from customers_manager_hub.channel_gateway import (
    ChannelAccountIdentity,
    ChannelMediaReference,
    ChannelProviderError,
    ChannelSendResult,
)
from customers_manager_hub.channel_models import ChannelCapability, ChannelType

_LOCAL_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def normalize_website_origin(value: str) -> str:
    candidate = value.strip()
    if not candidate or candidate == "null" or "*" in candidate:
        raise ValueError("Website origin must be an exact non-wildcard origin")
    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"} or parts.hostname is None:
        raise ValueError("Website origin must use http or https and include a host")
    if parts.username is not None or parts.password is not None:
        raise ValueError("Website origin must not contain user information")
    if parts.path not in {"", "/"} or parts.query or parts.fragment:
        raise ValueError("Website origin must not contain a path, query, or fragment")
    host = parts.hostname.lower()
    if ":" not in host:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Website origin contains an invalid host") from exc
    if parts.scheme == "http" and host not in _LOCAL_HTTP_HOSTS:
        raise ValueError("Non-local Website origins must use https")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("Website origin contains an invalid port") from exc
    if (parts.scheme, port) in {("http", 80), ("https", 443)}:
        port = None
    rendered_host = f"[{host}]" if ":" in host else host
    netloc = rendered_host if port is None else f"{rendered_host}:{port}"
    return f"{parts.scheme}://{netloc}"


class WebsiteAdapter:
    channel_type = ChannelType.WEBSITE
    capabilities = frozenset({ChannelCapability.TEXT, ChannelCapability.OUTBOUND_TEXT})

    async def validate_account(self, access_secret: str) -> ChannelAccountIdentity:
        del access_secret
        raise ChannelProviderError("website_external_account_unsupported", retryable=False)

    async def register_webhook(
        self,
        access_secret: str,
        *,
        webhook_url: str,
        webhook_secret: str,
    ) -> None:
        del access_secret, webhook_url, webhook_secret
        raise ChannelProviderError("website_webhook_unsupported", retryable=False)

    async def resolve_media(
        self,
        access_secret: str,
        external_media_id: str,
    ) -> ChannelMediaReference:
        del access_secret, external_media_id
        raise ChannelProviderError("website_media_unsupported", retryable=False)

    async def send_text(
        self,
        access_secret: str,
        *,
        external_thread_id: str,
        text: str,
    ) -> ChannelSendResult:
        del access_secret, external_thread_id, text
        raise ChannelProviderError("website_external_send_unsupported", retryable=False)
