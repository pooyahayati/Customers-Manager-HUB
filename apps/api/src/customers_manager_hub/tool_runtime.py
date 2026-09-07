import asyncio
import ipaddress
import json
import re
import socket
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Protocol, cast
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID

import httpx2
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from customers_manager_hub.agent_models import Agent
from customers_manager_hub.config import Settings
from customers_manager_hub.database import AsyncSessionFactory
from customers_manager_hub.models import AuditEvent
from customers_manager_hub.policy_models import PolicyDecisionAction
from customers_manager_hub.policy_runtime import PolicyEngine, PolicyRuntimeError
from customers_manager_hub.tool_models import (
    AgentToolPermission,
    ToolAdapterKind,
    ToolApprovalStatus,
    ToolAuthType,
    ToolCredential,
    ToolDefinition,
    ToolExecution,
    ToolExecutionStatus,
    ToolOperationType,
    ToolRiskLevel,
)
from customers_manager_hub.tool_security import decrypt_tool_secret

_SCHEMA_SIZE_LIMIT = 32_000
_INPUT_SIZE_LIMIT = 64_000
_OUTPUT_SIZE_LIMIT = 128_000
_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,98}[a-z0-9]$|^[a-z]$")
_GOOGLE_SPREADSHEET_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
_HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}$")
_FORBIDDEN_STATIC_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "host",
        "proxy-authorization",
        "proxy-connection",
        "content-length",
        "transfer-encoding",
    }
)
_RETRYABLE_HTTP_STATUSES = frozenset({408, 409, 425, 429})
_GOOGLE_SHEETS_HOST = "sheets.googleapis.com"


class ToolRuntimeError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class ToolAdapterError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ModelToolDefinition:
    id: UUID
    qualified_name: str
    description: str
    operation_type: ToolOperationType
    risk_level: ToolRiskLevel
    input_schema: dict[str, object]


@dataclass(frozen=True, slots=True)
class ResolvedToolCredential:
    auth_type: ToolAuthType
    secret: str
    header_name: str | None


@dataclass(frozen=True, slots=True)
class ToolAdapterRequest:
    tool_id: UUID
    operation_type: ToolOperationType
    configuration: dict[str, object]
    arguments: dict[str, object]
    credential: ResolvedToolCredential | None
    execution_token: str
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    execution_id: UUID
    tool_name: str
    status: ToolExecutionStatus
    output: dict[str, object] | None
    error_code: str | None

    def model_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "execution_id": str(self.execution_id),
            "tool": self.tool_name,
            "status": self.status.value,
        }
        if self.output is not None:
            payload["result"] = self.output
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        return payload


Resolver = Callable[[str, int], Awaitable[tuple[str, ...]]]


class ToolAdapter(Protocol):
    @property
    def kind(self) -> ToolAdapterKind: ...

    async def execute(self, request: ToolAdapterRequest) -> dict[str, object]: ...


class ToolAdapterRegistry:
    def __init__(self, adapters: Iterable[ToolAdapter] = ()) -> None:
        self._adapters: dict[ToolAdapterKind, ToolAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ToolAdapter) -> None:
        if adapter.kind in self._adapters:
            raise ValueError(f"Tool adapter already registered: {adapter.kind.value}")
        self._adapters[adapter.kind] = adapter

    def get(self, kind: ToolAdapterKind) -> ToolAdapter:
        adapter = self._adapters.get(kind)
        if adapter is None:
            raise ToolRuntimeError("tool_adapter_unavailable")
        return adapter


def qualified_tool_name(name: str, version: int) -> str:
    return f"{name}@{version}"


def normalize_tool_name(value: str) -> str:
    normalized = value.strip().casefold()
    if not _TOOL_NAME_PATTERN.fullmatch(normalized):
        raise ValueError("Tool name must use lowercase letters, digits, dot, underscore, or hyphen")
    return normalized


def _contains_ref(value: object) -> bool:
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        if "$ref" in mapping:
            return True
        return any(_contains_ref(item) for item in mapping.values())
    if isinstance(value, list):
        return any(_contains_ref(item) for item in cast(list[object], value))
    return False


def _bounded_json(value: object, limit: int, *, code: str) -> str:
    try:
        serialized = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(code) from exc
    if len(serialized.encode()) > limit:
        raise ValueError(code)
    return serialized


def validate_tool_schema(schema: dict[str, object]) -> dict[str, object]:
    _bounded_json(schema, _SCHEMA_SIZE_LIMIT, code="Tool schema is too large")
    if _contains_ref(schema):
        raise ValueError("Tool schema must be self-contained and must not use $ref")
    if schema.get("type") != "object":
        raise ValueError("Tool schema must describe a top-level object")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError("Tool schema is not valid Draft 2020-12 JSON Schema") from exc
    return schema


def validate_tool_payload(
    schema: dict[str, object],
    payload: dict[str, object],
    *,
    output: bool = False,
) -> dict[str, object]:
    limit = _OUTPUT_SIZE_LIMIT if output else _INPUT_SIZE_LIMIT
    _bounded_json(payload, limit, code="tool_payload_too_large")
    try:
        Draft202012Validator(schema).validate(payload)  # pyright: ignore[reportUnknownMemberType]
    except JsonSchemaValidationError as exc:
        code = "tool_output_schema_invalid" if output else "tool_input_schema_invalid"
        raise ToolRuntimeError(code) from exc
    return payload


def normalize_header_name(value: str) -> str:
    normalized = value.strip()
    if not _HEADER_NAME_PATTERN.fullmatch(normalized):
        raise ValueError("Invalid HTTP header name")
    lowered = normalized.casefold()
    if lowered in {"host", "content-length", "transfer-encoding", "proxy-authorization"}:
        raise ValueError("Header name is not permitted for tool credentials")
    return normalized


def normalize_fixed_https_url(value: str) -> str:
    candidate = value.strip()
    parts = urlsplit(candidate)
    if parts.scheme != "https" or parts.hostname is None:
        raise ValueError("Tool URL must use HTTPS and include a host")
    if parts.username is not None or parts.password is not None:
        raise ValueError("Tool URL must not contain user information")
    if parts.fragment:
        raise ValueError("Tool URL must not contain a fragment")
    host = parts.hostname.rstrip(".").casefold()
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Tool URL host is not permitted")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Tool URL contains an invalid host") from exc
    else:
        if not literal.is_global:
            raise ValueError("Tool URL IP address must be globally routable")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("Tool URL contains an invalid port") from exc
    rendered_host = f"[{host}]" if ":" in host else host
    netloc = rendered_host if port in {None, 443} else f"{rendered_host}:{port}"
    return urlunsplit(("https", netloc, parts.path or "/", parts.query, ""))


async def resolve_host_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ToolAdapterError("tool_dns_resolution_failed", retryable=True) from exc
    addresses = tuple(sorted({str(item[4][0]) for item in infos}))
    if not addresses:
        raise ToolAdapterError("tool_dns_resolution_failed", retryable=True)
    return addresses


async def ensure_public_destination(url: str, resolver: Resolver) -> None:
    parts = urlsplit(url)
    host = parts.hostname
    if host is None:
        raise ToolAdapterError("tool_destination_invalid", retryable=False)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise ToolAdapterError("tool_ssrf_destination_denied", retryable=False)
        return
    addresses = await resolver(host, parts.port or 443)
    for address in addresses:
        try:
            resolved = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ToolAdapterError("tool_dns_resolution_invalid", retryable=False) from exc
        if not resolved.is_global:
            raise ToolAdapterError("tool_ssrf_destination_denied", retryable=False)


def _mapping(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    mapping = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for key, item in mapping.items():
        if not isinstance(key, str):
            raise ValueError(f"{field} keys must be strings")
        result[key] = item
    return result


def _static_headers(value: object) -> dict[str, str]:
    if value is None:
        return {}
    mapping = _mapping(value, field="static_headers")
    headers: dict[str, str] = {}
    for key, item in mapping.items():
        normalized_name = normalize_header_name(key)
        if normalized_name.casefold() in _FORBIDDEN_STATIC_HEADERS:
            raise ValueError("Sensitive HTTP headers must use credential binding")
        if not isinstance(item, str) or len(item) > 1000:
            raise ValueError("Static HTTP header values must be bounded strings")
        headers[normalized_name] = item
    return headers


def normalize_tool_configuration(
    adapter_kind: ToolAdapterKind,
    operation_type: ToolOperationType,
    configuration: dict[str, object],
) -> dict[str, object]:
    if adapter_kind in {ToolAdapterKind.GENERIC_REST, ToolAdapterKind.BUSINESS_REFERENCE}:
        allowed = {"url", "method", "argument_location", "static_headers", "idempotency_header"}
        if set(configuration) - allowed:
            raise ValueError("REST tool configuration contains unsupported fields")
        url_value = configuration.get("url")
        if not isinstance(url_value, str):
            raise ValueError("REST tool configuration requires url")
        url = normalize_fixed_https_url(url_value)
        method_value = configuration.get("method", "GET")
        if not isinstance(method_value, str):
            raise ValueError("REST tool method must be a string")
        method = method_value.strip().upper()
        location_value = configuration.get("argument_location", "query")
        if not isinstance(location_value, str) or location_value not in {"query", "json"}:
            raise ValueError("argument_location must be query or json")
        if adapter_kind == ToolAdapterKind.BUSINESS_REFERENCE:
            if (
                operation_type != ToolOperationType.READ
                or method != "GET"
                or location_value != "query"
            ):
                raise ValueError("Business reference tools must be read-only GET query tools")
        elif operation_type == ToolOperationType.READ and method != "GET":
            raise ValueError("Read Generic REST tools must use GET")
        elif operation_type == ToolOperationType.WRITE and method not in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }:
            raise ValueError("Write Generic REST tools must use POST, PUT, PATCH, or DELETE")
        headers = _static_headers(configuration.get("static_headers"))
        idempotency_value = configuration.get("idempotency_header")
        idempotency_header: str | None = None
        if idempotency_value is not None:
            if not isinstance(idempotency_value, str):
                raise ValueError("idempotency_header must be a string")
            idempotency_header = normalize_header_name(idempotency_value)
            if idempotency_header.casefold() in _FORBIDDEN_STATIC_HEADERS:
                raise ValueError("Invalid idempotency header")
        normalized: dict[str, object] = {
            "url": url,
            "method": method,
            "argument_location": location_value,
            "static_headers": headers,
        }
        if idempotency_header is not None:
            normalized["idempotency_header"] = idempotency_header
        return normalized

    if adapter_kind == ToolAdapterKind.GOOGLE_SHEETS:
        if operation_type != ToolOperationType.READ:
            raise ValueError("Google Sheets M8 adapter is read-only")
        allowed = {"spreadsheet_id", "value_render_option"}
        if set(configuration) - allowed:
            raise ValueError("Google Sheets configuration contains unsupported fields")
        spreadsheet_id = configuration.get("spreadsheet_id")
        if not isinstance(spreadsheet_id, str) or not _GOOGLE_SPREADSHEET_ID_PATTERN.fullmatch(
            spreadsheet_id.strip()
        ):
            raise ValueError("Invalid Google Sheets spreadsheet_id")
        normalized = {"spreadsheet_id": spreadsheet_id.strip()}
        render = configuration.get("value_render_option")
        if render is not None:
            if render not in {"FORMATTED_VALUE", "UNFORMATTED_VALUE", "FORMULA"}:
                raise ValueError("Invalid Google Sheets value_render_option")
            normalized["value_render_option"] = render
        return normalized

    raise ValueError("Unsupported tool adapter kind")


def _query_params(arguments: Mapping[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in arguments.items():
        if value is None:
            continue
        if isinstance(value, bool):
            result[key] = "true" if value else "false"
        elif isinstance(value, (str, int, float)):
            result[key] = str(value)
        else:
            result[key] = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return result


def _response_object(response: httpx2.Response) -> dict[str, object]:
    try:
        payload: object = response.json()
    except ValueError as exc:
        raise ToolAdapterError("tool_invalid_json_response", retryable=False) from exc
    if isinstance(payload, dict):
        mapping = cast(dict[object, object], payload)
        if not all(isinstance(key, str) for key in mapping):
            raise ToolAdapterError("tool_invalid_json_response", retryable=False)
        return {cast(str, key): value for key, value in mapping.items()}
    return {"data": payload}


def _credential_headers(credential: ResolvedToolCredential | None) -> dict[str, str]:
    if credential is None:
        return {}
    if credential.auth_type == ToolAuthType.BEARER:
        return {"Authorization": f"Bearer {credential.secret}"}
    if credential.auth_type == ToolAuthType.HEADER:
        if credential.header_name is None:
            raise ToolAdapterError("tool_credential_invalid", retryable=False)
        return {credential.header_name: credential.secret}
    raise ToolAdapterError("tool_credential_invalid", retryable=False)


def _http_retryable(status_code: int) -> bool:
    return status_code in _RETRYABLE_HTTP_STATUSES or status_code >= 500


class RestToolAdapter:
    def __init__(
        self,
        client: httpx2.AsyncClient,
        *,
        kind: ToolAdapterKind = ToolAdapterKind.GENERIC_REST,
        resolver: Resolver = resolve_host_addresses,
    ) -> None:
        if kind not in {ToolAdapterKind.GENERIC_REST, ToolAdapterKind.BUSINESS_REFERENCE}:
            raise ValueError("RestToolAdapter kind must be a REST adapter kind")
        self._client = client
        self._kind = kind
        self._resolver = resolver

    @property
    def kind(self) -> ToolAdapterKind:
        return self._kind

    async def execute(self, request: ToolAdapterRequest) -> dict[str, object]:
        configuration = normalize_tool_configuration(
            self.kind,
            request.operation_type,
            request.configuration,
        )
        url = cast(str, configuration["url"])
        await ensure_public_destination(url, self._resolver)
        method = cast(str, configuration["method"])
        location = cast(str, configuration["argument_location"])
        static_headers = cast(dict[str, str], configuration["static_headers"])
        headers = dict(static_headers)
        headers.update(_credential_headers(request.credential))
        idempotency_header = configuration.get("idempotency_header")
        if isinstance(idempotency_header, str):
            headers[idempotency_header] = request.execution_token
        try:
            if location == "query":
                response = await self._client.request(
                    method,
                    url,
                    headers=headers,
                    params=_query_params(request.arguments),
                    timeout=float(request.timeout_seconds),
                    follow_redirects=False,
                )
            else:
                response = await self._client.request(
                    method,
                    url,
                    headers=headers,
                    json=request.arguments,
                    timeout=float(request.timeout_seconds),
                    follow_redirects=False,
                )
        except httpx2.TimeoutException as exc:
            raise ToolAdapterError("tool_http_timeout", retryable=True) from exc
        except httpx2.TransportError as exc:
            raise ToolAdapterError("tool_http_transport_error", retryable=True) from exc
        if 300 <= response.status_code < 400:
            raise ToolAdapterError("tool_http_redirect_denied", retryable=False)
        if response.status_code >= 400:
            raise ToolAdapterError(
                f"tool_http_{response.status_code}",
                retryable=_http_retryable(response.status_code),
            )
        return _response_object(response)


class GoogleSheetsToolAdapter:
    def __init__(
        self,
        client: httpx2.AsyncClient,
        *,
        resolver: Resolver = resolve_host_addresses,
    ) -> None:
        self._client = client
        self._resolver = resolver

    @property
    def kind(self) -> ToolAdapterKind:
        return ToolAdapterKind.GOOGLE_SHEETS

    async def execute(self, request: ToolAdapterRequest) -> dict[str, object]:
        configuration = normalize_tool_configuration(
            self.kind,
            request.operation_type,
            request.configuration,
        )
        spreadsheet_id = cast(str, configuration["spreadsheet_id"])
        range_value = request.arguments.get("range")
        if not isinstance(range_value, str) or not range_value.strip() or len(range_value) > 500:
            raise ToolAdapterError("tool_google_sheets_range_invalid", retryable=False)
        if request.credential is None or request.credential.auth_type != ToolAuthType.BEARER:
            raise ToolAdapterError("tool_credential_missing", retryable=False)
        url = (
            f"https://{_GOOGLE_SHEETS_HOST}/v4/spreadsheets/"
            f"{quote(spreadsheet_id, safe='')}/values/{quote(range_value.strip(), safe='')}"
        )
        await ensure_public_destination(url, self._resolver)
        params: dict[str, str] = {}
        render = configuration.get("value_render_option")
        if isinstance(render, str):
            params["valueRenderOption"] = render
        try:
            response = await self._client.get(
                url,
                headers=_credential_headers(request.credential),
                params=params,
                timeout=float(request.timeout_seconds),
                follow_redirects=False,
            )
        except httpx2.TimeoutException as exc:
            raise ToolAdapterError("tool_google_sheets_timeout", retryable=True) from exc
        except httpx2.TransportError as exc:
            raise ToolAdapterError("tool_google_sheets_transport_error", retryable=True) from exc
        if 300 <= response.status_code < 400:
            raise ToolAdapterError("tool_http_redirect_denied", retryable=False)
        if response.status_code >= 400:
            raise ToolAdapterError(
                f"tool_google_sheets_http_{response.status_code}",
                retryable=_http_retryable(response.status_code),
            )
        return _response_object(response)


def build_tool_adapter_registry(
    client: httpx2.AsyncClient,
    *,
    resolver: Resolver = resolve_host_addresses,
) -> ToolAdapterRegistry:
    return ToolAdapterRegistry(
        (
            RestToolAdapter(client, resolver=resolver),
            RestToolAdapter(client, kind=ToolAdapterKind.BUSINESS_REFERENCE, resolver=resolver),
            GoogleSheetsToolAdapter(client, resolver=resolver),
        )
    )


class ToolRuntime:
    def __init__(
        self,
        settings: Settings,
        session_factory: AsyncSessionFactory,
        registry: ToolAdapterRegistry,
        policy_engine: PolicyEngine | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._registry = registry
        self._policy_engine = policy_engine

    async def list_agent_tools(
        self,
        tenant_id: UUID,
        agent_id: UUID,
    ) -> tuple[ModelToolDefinition, ...]:
        async with self._session_factory() as db:
            agent = await db.scalar(
                select(Agent).where(
                    Agent.id == agent_id,
                    Agent.tenant_id == tenant_id,
                    Agent.is_active.is_(True),
                )
            )
            if agent is None:
                return ()
            tools = list(
                (
                    await db.scalars(
                        select(ToolDefinition)
                        .join(
                            AgentToolPermission,
                            (AgentToolPermission.tool_id == ToolDefinition.id)
                            & (AgentToolPermission.tenant_id == ToolDefinition.tenant_id),
                        )
                        .where(
                            ToolDefinition.tenant_id == tenant_id,
                            ToolDefinition.is_active.is_(True),
                            AgentToolPermission.agent_id == agent_id,
                        )
                        .order_by(ToolDefinition.name, ToolDefinition.version, ToolDefinition.id)
                    )
                ).all()
            )
        return tuple(
            ModelToolDefinition(
                id=tool.id,
                qualified_name=qualified_tool_name(tool.name, tool.version),
                description=tool.description,
                operation_type=ToolOperationType(tool.operation_type),
                risk_level=ToolRiskLevel(tool.risk_level),
                input_schema=dict(tool.input_schema),
            )
            for tool in tools
        )

    async def _resolve_allowed_tool(
        self,
        tenant_id: UUID,
        agent_id: UUID,
        qualified_name: str,
    ) -> ToolDefinition:
        async with self._session_factory() as db:
            agent = await db.scalar(
                select(Agent).where(
                    Agent.id == agent_id,
                    Agent.tenant_id == tenant_id,
                    Agent.is_active.is_(True),
                )
            )
            if agent is None:
                raise ToolRuntimeError("tool_agent_unavailable")
            rows = list(
                (
                    await db.scalars(
                        select(ToolDefinition)
                        .join(
                            AgentToolPermission,
                            (AgentToolPermission.tool_id == ToolDefinition.id)
                            & (AgentToolPermission.tenant_id == ToolDefinition.tenant_id),
                        )
                        .where(
                            ToolDefinition.tenant_id == tenant_id,
                            ToolDefinition.is_active.is_(True),
                            AgentToolPermission.agent_id == agent_id,
                        )
                    )
                ).all()
            )
        for tool in rows:
            if qualified_tool_name(tool.name, tool.version) == qualified_name:
                return tool
        raise ToolRuntimeError("tool_not_authorized")

    async def _load_credential(self, tool: ToolDefinition) -> ResolvedToolCredential | None:
        async with self._session_factory() as db:
            credential = await db.scalar(
                select(ToolCredential).where(
                    ToolCredential.tenant_id == tool.tenant_id,
                    ToolCredential.tool_id == tool.id,
                )
            )
            if credential is None:
                return None
            auth_type = ToolAuthType(credential.auth_type)
            secret = decrypt_tool_secret(
                self._settings,
                tool.tenant_id,
                tool.id,
                auth_type,
                header_name=credential.header_name,
                ciphertext=credential.ciphertext,
                nonce=credential.nonce,
                key_version=credential.key_version,
            )
            return ResolvedToolCredential(
                auth_type=auth_type,
                secret=secret,
                header_name=credential.header_name,
            )

    @staticmethod
    def _approval_required(tool: ToolDefinition) -> bool:
        return tool.requires_approval or ToolRiskLevel(tool.risk_level) in {
            ToolRiskLevel.HIGH,
            ToolRiskLevel.CRITICAL,
        }

    async def _load_execution(
        self,
        tenant_id: UUID,
        idempotency_key: str,
    ) -> ToolExecution | None:
        async with self._session_factory() as db:
            return await db.scalar(
                select(ToolExecution).where(
                    ToolExecution.tenant_id == tenant_id,
                    ToolExecution.idempotency_key == idempotency_key,
                )
            )

    @staticmethod
    def _result(execution: ToolExecution, tool_name: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            execution_id=execution.id,
            tool_name=tool_name,
            status=ToolExecutionStatus(execution.status),
            output=dict(execution.output_payload) if execution.output_payload is not None else None,
            error_code=execution.error_code,
        )

    async def _create_execution(
        self,
        *,
        tool: ToolDefinition,
        agent_id: UUID,
        agent_run_id: UUID,
        call_ordinal: int,
        idempotency_key: str,
        arguments: dict[str, object],
        approval_required: bool,
        denied: bool = False,
        error_code: str | None = None,
    ) -> ToolExecution:
        async with self._session_factory() as db:
            execution = ToolExecution(
                tenant_id=tool.tenant_id,
                tool_id=tool.id,
                agent_id=agent_id,
                agent_run_id=agent_run_id,
                call_ordinal=call_ordinal,
                idempotency_key=idempotency_key,
                status=(
                    ToolExecutionStatus.DENIED.value
                    if denied
                    else (
                        ToolExecutionStatus.APPROVAL_REQUIRED.value
                        if approval_required
                        else ToolExecutionStatus.PENDING.value
                    )
                ),
                approval_status=(
                    ToolApprovalStatus.DENIED.value
                    if denied
                    else (
                        ToolApprovalStatus.PENDING.value
                        if approval_required
                        else ToolApprovalStatus.NOT_REQUIRED.value
                    )
                ),
                input_payload=arguments,
                error_code=error_code[:100] if error_code is not None else None,
            )
            db.add(execution)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                raced = await db.scalar(
                    select(ToolExecution).where(
                        ToolExecution.tenant_id == tool.tenant_id,
                        ToolExecution.idempotency_key == idempotency_key,
                    )
                )
                if raced is None:
                    raise
                return raced
            await db.refresh(execution)
            return execution

    async def _mark_running(self, execution_id: UUID, tenant_id: UUID) -> None:
        async with self._session_factory() as db:
            execution = await db.scalar(
                select(ToolExecution)
                .where(
                    ToolExecution.id == execution_id,
                    ToolExecution.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if execution is None:
                raise ToolRuntimeError("tool_execution_missing")
            if execution.status == ToolExecutionStatus.RUNNING.value:
                raise ToolRuntimeError("tool_execution_busy", retryable=True)
            if execution.status != ToolExecutionStatus.PENDING.value:
                raise ToolRuntimeError("tool_execution_state_invalid")
            execution.status = ToolExecutionStatus.RUNNING.value
            execution.error_code = None
            await db.commit()

    async def _finish_execution(
        self,
        execution_id: UUID,
        tenant_id: UUID,
        *,
        status: ToolExecutionStatus,
        output: dict[str, object] | None,
        error_code: str | None,
        attempts: int,
        duration_ms: int,
        write_audit: bool,
    ) -> ToolExecution:
        async with self._session_factory() as db:
            execution = await db.scalar(
                select(ToolExecution).where(
                    ToolExecution.id == execution_id,
                    ToolExecution.tenant_id == tenant_id,
                )
            )
            if execution is None:
                raise ToolRuntimeError("tool_execution_missing")
            execution.status = status.value
            execution.output_payload = output
            execution.error_code = error_code[:100] if error_code is not None else None
            execution.attempt_count = attempts
            execution.duration_ms = duration_ms
            if write_audit:
                db.add(
                    AuditEvent(
                        tenant_id=tenant_id,
                        actor_user_id=None,
                        action=f"tool.execution.{status.value}",
                        target_type="tool_execution",
                        target_id=execution.id,
                        details={
                            "tool_id": str(execution.tool_id),
                            "agent_id": str(execution.agent_id),
                            "attempt_count": attempts,
                            "error_code": execution.error_code,
                        },
                    )
                )
            await db.commit()
            await db.refresh(execution)
            return execution

    async def execute_agent_tool(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        agent_run_id: UUID,
        call_ordinal: int,
        qualified_name: str,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        if call_ordinal < 1 or call_ordinal > 20:
            raise ToolRuntimeError("tool_call_ordinal_invalid")
        tool = await self._resolve_allowed_tool(tenant_id, agent_id, qualified_name)
        input_schema = validate_tool_schema(dict(tool.input_schema))
        normalized_arguments = validate_tool_payload(input_schema, arguments)
        idempotency_key = f"agent:{agent_run_id}:tool:{call_ordinal}"
        execution = await self._load_execution(tenant_id, idempotency_key)
        if execution is None:
            approval_required = self._approval_required(tool)
            denied = False
            policy_error_code: str | None = None
            if self._policy_engine is not None:
                try:
                    policy_decision = await self._policy_engine.evaluate_tool(
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        tool=tool,
                        baseline_approval_required=approval_required,
                    )
                except PolicyRuntimeError as exc:
                    raise ToolRuntimeError(exc.code, retryable=True) from exc
                approval_required = policy_decision.approval_required
                if policy_decision.action == PolicyDecisionAction.DENY:
                    denied = True
                    policy_error_code = policy_decision.reason_code
            execution = await self._create_execution(
                tool=tool,
                agent_id=agent_id,
                agent_run_id=agent_run_id,
                call_ordinal=call_ordinal,
                idempotency_key=idempotency_key,
                arguments=normalized_arguments,
                approval_required=approval_required,
                denied=denied,
                error_code=policy_error_code,
            )
        if execution.tool_id != tool.id or execution.agent_id != agent_id:
            raise ToolRuntimeError("tool_call_replay_mismatch")
        if dict(execution.input_payload) != normalized_arguments:
            raise ToolRuntimeError("tool_call_replay_mismatch")
        status = ToolExecutionStatus(execution.status)
        if status in {
            ToolExecutionStatus.SUCCEEDED,
            ToolExecutionStatus.FAILED,
            ToolExecutionStatus.DENIED,
            ToolExecutionStatus.APPROVAL_REQUIRED,
        }:
            return self._result(execution, qualified_name)
        if status == ToolExecutionStatus.RUNNING:
            stale_after = timedelta(
                seconds=max(60, tool.timeout_seconds * max(tool.max_attempts, 1) + 30)
            )
            if execution.updated_at >= datetime.now(UTC) - stale_after:
                raise ToolRuntimeError("tool_execution_busy", retryable=True)
            async with self._session_factory() as db:
                stale = await db.scalar(
                    select(ToolExecution)
                    .where(
                        ToolExecution.id == execution.id,
                        ToolExecution.tenant_id == tenant_id,
                    )
                    .with_for_update()
                )
                if stale is None:
                    raise ToolRuntimeError("tool_execution_missing")
                stale.status = ToolExecutionStatus.PENDING.value
                await db.commit()
                execution = stale

        if execution.approval_status == ToolApprovalStatus.PENDING.value:
            return self._result(execution, qualified_name)

        await self._mark_running(execution.id, tenant_id)
        configuration = normalize_tool_configuration(
            ToolAdapterKind(tool.adapter_kind),
            ToolOperationType(tool.operation_type),
            dict(tool.configuration),
        )
        credential = await self._load_credential(tool)
        adapter = self._registry.get(ToolAdapterKind(tool.adapter_kind))
        attempts_allowed = tool.max_attempts
        if ToolOperationType(tool.operation_type) == ToolOperationType.WRITE and not isinstance(
            configuration.get("idempotency_header"), str
        ):
            attempts_allowed = 1
        started = perf_counter()
        attempts = 0
        last_error: ToolAdapterError | None = None
        for attempt in range(1, attempts_allowed + 1):
            attempts = attempt
            try:
                output = await adapter.execute(
                    ToolAdapterRequest(
                        tool_id=tool.id,
                        operation_type=ToolOperationType(tool.operation_type),
                        configuration=configuration,
                        arguments=normalized_arguments,
                        credential=credential,
                        execution_token=str(execution.id),
                        timeout_seconds=tool.timeout_seconds,
                    )
                )
                validated_output = validate_tool_payload(
                    validate_tool_schema(dict(tool.output_schema)),
                    output,
                    output=True,
                )
            except ToolAdapterError as exc:
                last_error = exc
                if exc.retryable and attempt < attempts_allowed:
                    await asyncio.sleep(min(0.25 * (2 ** (attempt - 1)), 1.0))
                    continue
                duration_ms = max(0, round((perf_counter() - started) * 1000))
                failed = await self._finish_execution(
                    execution.id,
                    tenant_id,
                    status=ToolExecutionStatus.FAILED,
                    output=None,
                    error_code=exc.code,
                    attempts=attempts,
                    duration_ms=duration_ms,
                    write_audit=ToolOperationType(tool.operation_type) == ToolOperationType.WRITE,
                )
                return self._result(failed, qualified_name)
            except ToolRuntimeError as exc:
                duration_ms = max(0, round((perf_counter() - started) * 1000))
                failed = await self._finish_execution(
                    execution.id,
                    tenant_id,
                    status=ToolExecutionStatus.FAILED,
                    output=None,
                    error_code=exc.code,
                    attempts=attempts,
                    duration_ms=duration_ms,
                    write_audit=ToolOperationType(tool.operation_type) == ToolOperationType.WRITE,
                )
                return self._result(failed, qualified_name)
            duration_ms = max(0, round((perf_counter() - started) * 1000))
            succeeded = await self._finish_execution(
                execution.id,
                tenant_id,
                status=ToolExecutionStatus.SUCCEEDED,
                output=validated_output,
                error_code=None,
                attempts=attempts,
                duration_ms=duration_ms,
                write_audit=ToolOperationType(tool.operation_type) == ToolOperationType.WRITE,
            )
            return self._result(succeeded, qualified_name)

        if last_error is not None:
            raise ToolRuntimeError(last_error.code)
        raise ToolRuntimeError("tool_execution_failed")
