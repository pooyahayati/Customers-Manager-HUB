import asyncio
import io
import json
import logging
import zipfile
from typing import cast

import pytest
from pydantic import ValidationError
from redis.asyncio import Redis
from starlette.requests import Request
from starlette.types import Scope

from customers_manager_hub.config import Settings
from customers_manager_hub.knowledge_parsing import KnowledgeParseError, parse_pdf, parse_xlsx
from customers_manager_hub.logging_config import JsonFormatter
from customers_manager_hub.production_hardening import (
    RateLimitRule,
    RedisRateLimiter,
    client_ip,
    match_rate_limit_rule,
    normalize_request_id,
)

_VALID_ENCRYPTION_KEY = "dmFsaWRfdGVzdF9rZXlfMzJfYnl0ZXNfbG9uZ19fX18="


class FakeRateRedis:
    def __init__(self) -> None:
        self.count = 0
        self.keys: list[str] = []

    async def eval(self, script: str, numkeys: int, key: str, window: int) -> list[int]:
        del script, numkeys
        self.keys.append(key)
        self.count += 1
        return [self.count, window]


def _request(
    path: str,
    *,
    method: str = "GET",
    client: str = "203.0.113.20",
    headers: tuple[tuple[bytes, bytes], ...] = (),
) -> Request:
    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": list(headers),
            "client": (client, 44321),
            "server": ("api.example.test", 443),
        },
    )
    return Request(scope)


def test_request_id_accepts_only_bounded_safe_values() -> None:
    assert normalize_request_id("trace-123") == "trace-123"
    generated = normalize_request_id("unsafe value with spaces")
    assert len(generated) == 32
    assert generated != "unsafe value with spaces"


def test_client_ip_trusts_forwarding_only_from_configured_proxy() -> None:
    settings = Settings(trusted_proxy_cidrs="10.0.0.0/8", _env_file=None)  # pyright: ignore[reportCallIssue]
    forwarded = ((b"x-forwarded-for", b"198.51.100.25, 10.0.0.1"),)

    assert client_ip(_request("/", client="10.1.2.3", headers=forwarded), settings) == "198.51.100.25"
    assert client_ip(_request("/", client="192.0.2.50", headers=forwarded), settings) == "192.0.2.50"


def test_rate_limit_rule_matches_only_supported_public_ingress() -> None:
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    login = match_rate_limit_rule(_request("/api/v1/auth/login", method="POST"), settings)
    website = match_rate_limit_rule(
        _request("/api/v1/public/website/channel/sessions", method="POST"), settings
    )
    webhook = match_rate_limit_rule(
        _request("/api/v1/webhooks/telegram/channel", method="POST"), settings
    )

    assert login == RateLimitRule("auth-login", 10, 60)
    assert website == RateLimitRule("website-public", 120, 60)
    assert webhook == RateLimitRule("telegram-webhook", 180, 60)
    assert match_rate_limit_rule(_request("/health"), settings) is None


def test_rate_limiter_hashes_identity_and_enforces_limit() -> None:
    fake = FakeRateRedis()
    limiter = RedisRateLimiter(cast(Redis, fake))
    rule = RateLimitRule("test", 1, 60)

    first = asyncio.run(limiter.check(rule, "203.0.113.7"))
    second = asyncio.run(limiter.check(rule, "203.0.113.7"))

    assert first is not None and first.allowed is True
    assert second is not None and second.allowed is False
    assert fake.keys
    assert all("203.0.113.7" not in key for key in fake.keys)


def test_production_configuration_rejects_insecure_runtime_options() -> None:
    secure_database = "postgresql://cmh:generated-password@postgres:5432/customers_manager_hub"

    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            database_url=secure_database,
            _env_file=None,  # pyright: ignore[reportCallIssue]
        )
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            database_url=secure_database,
            encryption_key=_VALID_ENCRYPTION_KEY,
            app_debug=True,
            _env_file=None,  # pyright: ignore[reportCallIssue]
        )
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            database_url=secure_database,
            encryption_key=_VALID_ENCRYPTION_KEY,
            rate_limit_enabled=False,
            _env_file=None,  # pyright: ignore[reportCallIssue]
        )

    settings = Settings(
        app_env="production",
        database_url=secure_database,
        encryption_key=_VALID_ENCRYPTION_KEY,
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )
    assert settings.app_debug is False
    assert settings.rate_limit_enabled is True


def test_blank_development_encryption_key_is_treated_as_unset() -> None:
    settings = Settings(encryption_key="", _env_file=None)  # pyright: ignore[reportCallIssue]
    assert settings.encryption_key is None


def test_pdf_requires_pdf_signature_before_parser_work() -> None:
    with pytest.raises(KnowledgeParseError) as raised:
        parse_pdf(b"not-a-pdf")
    assert raised.value.code == "knowledge_pdf_signature_invalid"


def test_xlsx_rejects_parent_traversal_archive_entry() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("../outside.xml", "unsafe")

    with pytest.raises(KnowledgeParseError) as raised:
        parse_xlsx(buffer.getvalue())
    assert raised.value.code == "knowledge_xlsx_unsafe_path"


def test_xlsx_rejects_extreme_compression_ratio() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", b"0" * 1_000_000)

    with pytest.raises(KnowledgeParseError) as raised:
        parse_xlsx(buffer.getvalue())
    assert raised.value.code == "knowledge_xlsx_compression_ratio_too_high"


def test_json_logging_retains_only_allowlisted_operational_metadata() -> None:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "message", (), None)
    record.event_id = "event-123"
    record.error_code = "example_error"
    record.secret = "must-not-appear"

    payload = cast(dict[str, object], json.loads(JsonFormatter().format(record)))
    assert payload["event_id"] == "event-123"
    assert payload["error_code"] == "example_error"
    assert "secret" not in payload
