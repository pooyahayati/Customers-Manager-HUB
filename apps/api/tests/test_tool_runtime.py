import asyncio
from typing import cast
from uuid import uuid4

import httpx2
import pytest

from customers_manager_hub.tool_models import (
    ToolAdapterKind,
    ToolAuthType,
    ToolOperationType,
)
from customers_manager_hub.tool_runtime import (
    GoogleSheetsToolAdapter,
    ResolvedToolCredential,
    RestToolAdapter,
    ToolAdapterError,
    ToolAdapterRequest,
    ToolRuntimeError,
    ensure_public_destination,
    normalize_fixed_https_url,
    normalize_tool_configuration,
    normalize_tool_name,
    validate_tool_payload,
    validate_tool_schema,
)


async def public_resolver(host: str, port: int) -> tuple[str, ...]:
    assert host
    assert port == 443
    return ("93.184.216.34",)


def object_schema(properties: dict[str, object], required: list[str]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def test_tool_schema_payload_name_and_configuration_validation() -> None:
    schema = object_schema({"sku": {"type": "string"}}, ["sku"])
    assert validate_tool_schema(schema) == schema
    assert validate_tool_payload(schema, {"sku": "A-1"}) == {"sku": "A-1"}

    with pytest.raises(ToolRuntimeError, match="tool_input_schema_invalid"):
        validate_tool_payload(schema, {"sku": 1})
    with pytest.raises(ValueError, match="top-level object"):
        validate_tool_schema({"type": "array"})
    with pytest.raises(ValueError, match="must not use \\$ref"):
        validate_tool_schema(
            {
                "type": "object",
                "properties": {"sku": {"$ref": "#/defs/sku"}},
            }
        )
    assert normalize_tool_name(" Inventory.Lookup ") == "inventory.lookup"
    with pytest.raises(ValueError, match="lowercase letters"):
        normalize_tool_name("bad tool")

    assert normalize_fixed_https_url("https://EXAMPLE.com:443/v1/items") == (
        "https://example.com/v1/items"
    )
    for url in (
        "http://example.com/items",
        "https://localhost/items",
        "https://127.0.0.1/items",
        "https://10.0.0.1/items",
    ):
        with pytest.raises(ValueError):
            normalize_fixed_https_url(url)

    read_config = normalize_tool_configuration(
        ToolAdapterKind.GENERIC_REST,
        ToolOperationType.READ,
        {
            "url": "https://api.example.com/products",
            "method": "GET",
            "argument_location": "query",
        },
    )
    assert read_config["method"] == "GET"

    with pytest.raises(ValueError, match="Read Generic REST tools must use GET"):
        normalize_tool_configuration(
            ToolAdapterKind.GENERIC_REST,
            ToolOperationType.READ,
            {"url": "https://api.example.com/products", "method": "POST"},
        )
    with pytest.raises(ValueError, match="Business reference tools must be read-only"):
        normalize_tool_configuration(
            ToolAdapterKind.BUSINESS_REFERENCE,
            ToolOperationType.WRITE,
            {"url": "https://api.example.com/products", "method": "POST"},
        )
    with pytest.raises(ValueError, match="Sensitive HTTP headers"):
        normalize_tool_configuration(
            ToolAdapterKind.GENERIC_REST,
            ToolOperationType.READ,
            {
                "url": "https://api.example.com/products",
                "static_headers": {"Authorization": "should-not-be-configured-here"},
            },
        )
    with pytest.raises(ValueError, match="read-only"):
        normalize_tool_configuration(
            ToolAdapterKind.GOOGLE_SHEETS,
            ToolOperationType.WRITE,
            {"spreadsheet_id": "abcdefghijk"},
        )


def test_ssrf_destination_rejects_private_or_mixed_dns_results() -> None:
    async def private_resolver(host: str, port: int) -> tuple[str, ...]:
        del host, port
        return ("10.0.0.7",)

    async def mixed_resolver(host: str, port: int) -> tuple[str, ...]:
        del host, port
        return ("93.184.216.34", "192.168.1.20")

    async def run() -> None:
        await ensure_public_destination("https://93.184.216.34/path", public_resolver)
        with pytest.raises(ToolAdapterError, match="tool_ssrf_destination_denied"):
            await ensure_public_destination("https://api.example.test/path", private_resolver)
        with pytest.raises(ToolAdapterError, match="tool_ssrf_destination_denied"):
            await ensure_public_destination("https://api.example.test/path", mixed_resolver)
        with pytest.raises(ToolAdapterError, match="tool_ssrf_destination_denied"):
            await ensure_public_destination("https://127.0.0.1/path", public_resolver)

    asyncio.run(run())


def test_rest_adapter_injects_server_credential_and_idempotency_header() -> None:
    secret = "server-only-business-secret"

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert request.url.host == "api.example.test"
        assert request.headers["authorization"] == f"Bearer {secret}"
        assert request.headers["idempotency-key"] == "execution-token-123"
        body = cast(dict[str, object], request.json())
        assert body == {"sku": "A-1", "quantity": 2}
        return httpx2.Response(
            200,
            request=request,
            json={"ok": True, "stock": 7},
        )

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            adapter = RestToolAdapter(client, resolver=public_resolver)
            result = await adapter.execute(
                ToolAdapterRequest(
                    tool_id=uuid4(),
                    operation_type=ToolOperationType.WRITE,
                    configuration={
                        "url": "https://api.example.test/v1/reserve",
                        "method": "POST",
                        "argument_location": "json",
                        "idempotency_header": "Idempotency-Key",
                    },
                    arguments={"sku": "A-1", "quantity": 2},
                    credential=ResolvedToolCredential(
                        auth_type=ToolAuthType.BEARER,
                        secret=secret,
                        header_name=None,
                    ),
                    execution_token="execution-token-123",
                    timeout_seconds=5,
                )
            )
            assert result == {"ok": True, "stock": 7}
            assert secret not in repr(result)

    asyncio.run(run())


def test_rest_adapter_denies_redirects() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            302,
            request=request,
            headers={"location": "https://internal.example.test/secret"},
        )

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            adapter = RestToolAdapter(client, resolver=public_resolver)
            with pytest.raises(ToolAdapterError, match="tool_http_redirect_denied") as error:
                await adapter.execute(
                    ToolAdapterRequest(
                        tool_id=uuid4(),
                        operation_type=ToolOperationType.READ,
                        configuration={"url": "https://api.example.test/data", "method": "GET"},
                        arguments={"id": "123"},
                        credential=None,
                        execution_token="read-token",
                        timeout_seconds=5,
                    )
                )
            assert error.value.retryable is False

    asyncio.run(run())


def test_google_sheets_adapter_uses_fixed_google_endpoint_and_bearer() -> None:
    secret = "google-server-secret"

    async def google_resolver(host: str, port: int) -> tuple[str, ...]:
        assert host == "sheets.googleapis.com"
        assert port == 443
        return ("142.250.72.42",)

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "GET"
        assert request.url.host == "sheets.googleapis.com"
        assert request.url.path == "/v4/spreadsheets/abcdefghijk/values/Products%21A1%3AC20"
        assert request.url.params["valueRenderOption"] == "UNFORMATTED_VALUE"
        assert request.headers["authorization"] == f"Bearer {secret}"
        return httpx2.Response(
            200,
            request=request,
            json={"range": "Products!A1:C20", "values": [["SKU", "Stock"], ["A-1", 7]]},
        )

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            adapter = GoogleSheetsToolAdapter(client, resolver=google_resolver)
            result = await adapter.execute(
                ToolAdapterRequest(
                    tool_id=uuid4(),
                    operation_type=ToolOperationType.READ,
                    configuration={
                        "spreadsheet_id": "abcdefghijk",
                        "value_render_option": "UNFORMATTED_VALUE",
                    },
                    arguments={"range": "Products!A1:C20"},
                    credential=ResolvedToolCredential(
                        auth_type=ToolAuthType.BEARER,
                        secret=secret,
                        header_name=None,
                    ),
                    execution_token="sheet-read-token",
                    timeout_seconds=5,
                )
            )
            assert result["range"] == "Products!A1:C20"
            assert secret not in repr(result)

            with pytest.raises(ToolAdapterError, match="tool_credential_missing"):
                await adapter.execute(
                    ToolAdapterRequest(
                        tool_id=uuid4(),
                        operation_type=ToolOperationType.READ,
                        configuration={"spreadsheet_id": "abcdefghijk"},
                        arguments={"range": "Products!A1:C20"},
                        credential=None,
                        execution_token="sheet-read-token-2",
                        timeout_seconds=5,
                    )
                )

    asyncio.run(run())
