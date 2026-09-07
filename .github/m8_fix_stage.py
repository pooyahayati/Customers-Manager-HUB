from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text()
    if old not in text:
        raise RuntimeError(f"patch anchor not found in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1))


replace_once(
    "apps/api/src/customers_manager_hub/agent_runtime.py",
    '''    working_input = model_input
    tool_calls = 0
    for _round in range(_MAX_TOOL_ROUNDS):
''',
    '''    working_input = model_input
    tool_calls = 0
    rounds = 0
    while rounds < _MAX_TOOL_ROUNDS:
        rounds += 1
''',
)

replace_once(
    "apps/api/src/customers_manager_hub/tool_runtime.py",
    '''    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        addresses = await resolver(host, parts.port or 443)
        for address in addresses:
            try:
                resolved = ipaddress.ip_address(address)
            except ValueError as exc:
                raise ToolAdapterError("tool_dns_resolution_invalid", retryable=False) from exc
            if not resolved.is_global:
                raise ToolAdapterError("tool_ssrf_destination_denied", retryable=False)
    else:
        if not literal.is_global:
            raise ToolAdapterError("tool_ssrf_destination_denied", retryable=False)
''',
    '''    try:
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
''',
)

replace_once(
    "apps/api/src/customers_manager_hub/tool_runtime.py",
    '''        kwargs: dict[str, object] = {
            "headers": headers,
            "timeout": float(request.timeout_seconds),
            "follow_redirects": False,
        }
        if location == "query":
            kwargs["params"] = _query_params(request.arguments)
        else:
            kwargs["json"] = request.arguments
        try:
            response = await self._client.request(method, url, **kwargs)
''',
    '''        try:
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
''',
)

print("M8 staging fixes applied")
