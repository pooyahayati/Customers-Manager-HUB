import contextvars
import hashlib
import ipaddress
import logging
import re
import secrets
from dataclasses import dataclass
from time import perf_counter
from typing import cast

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from customers_manager_hub.config import Settings

logger = logging.getLogger(__name__)
REQUEST_ID_CONTEXT: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "cmh_request_id", default=None
)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    name: str
    limit: int
    window_seconds: int


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


def current_request_id() -> str | None:
    return REQUEST_ID_CONTEXT.get()


def normalize_request_id(value: str | None) -> str:
    if value is not None:
        candidate = value.strip()
        if _REQUEST_ID_PATTERN.fullmatch(candidate):
            return candidate
    return secrets.token_hex(16)


def _trusted_proxy_networks(
    settings: Settings,
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in settings.trusted_proxy_cidrs.split(","):
        candidate = item.strip()
        if candidate:
            networks.append(ipaddress.ip_network(candidate, strict=False))
    return tuple(networks)


def client_ip(request: Request, settings: Settings) -> str:
    peer = request.client.host if request.client is not None else "unknown"
    try:
        peer_address = ipaddress.ip_address(peer)
    except ValueError:
        return peer

    if any(peer_address in network for network in _trusted_proxy_networks(settings)):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            candidate = forwarded.split(",", 1)[0].strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                logger.warning("invalid forwarded client address")
    return str(peer_address)


def match_rate_limit_rule(request: Request, settings: Settings) -> RateLimitRule | None:
    path = request.url.path
    method = request.method.upper()
    window = settings.rate_limit_window_seconds
    if method == "POST" and path == "/api/v1/auth/login":
        return RateLimitRule("auth-login", settings.rate_limit_login_requests, window)
    if method != "OPTIONS" and path.startswith("/api/v1/public/website/"):
        return RateLimitRule("website-public", settings.rate_limit_public_requests, window)
    if method == "POST" and path.startswith("/api/v1/webhooks/telegram/"):
        return RateLimitRule("telegram-webhook", settings.rate_limit_webhook_requests, window)
    return None


class RedisRateLimiter:
    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

    async def check(self, rule: RateLimitRule, identity: str) -> RateLimitDecision | None:
        identity_hash = hashlib.sha256(identity.encode()).hexdigest()[:24]
        key = f"cmh:rate-limit:v1:{rule.name}:{identity_hash}"
        try:
            raw = await self._redis.eval(  # pyright: ignore[reportUnknownMemberType]
                _RATE_LIMIT_SCRIPT,
                1,
                key,
                rule.window_seconds,
            )
        except RedisError:
            logger.warning("rate limiter unavailable", extra={"rate_limit_rule": rule.name})
            return None
        result = cast(list[object], raw)
        if len(result) != 2 or not all(isinstance(item, int) for item in result):
            logger.warning(
                "rate limiter returned invalid response",
                extra={"rate_limit_rule": rule.name},
            )
            return None
        count = cast(int, result[0])
        ttl = max(cast(int, result[1]), 1)
        return RateLimitDecision(
            allowed=count <= rule.limit,
            limit=rule.limit,
            remaining=max(rule.limit - count, 0),
            retry_after=ttl,
        )


class ProductionHardeningMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
        rate_limiter: RedisRateLimiter,
    ) -> None:
        super().__init__(app)
        self._settings = settings
        self._rate_limiter = rate_limiter

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = normalize_request_id(request.headers.get("x-request-id"))
        context_token = REQUEST_ID_CONTEXT.set(request_id)
        started = perf_counter()
        response: Response | None = None
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        try:
            rule = match_rate_limit_rule(request, self._settings)
            decision: RateLimitDecision | None = None
            if self._settings.rate_limit_enabled and rule is not None:
                decision = await self._rate_limiter.check(
                    rule,
                    client_ip(request, self._settings),
                )
                if decision is not None and not decision.allowed:
                    response = JSONResponse(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        content={"detail": "Rate limit exceeded"},
                    )
                    response.headers["Retry-After"] = str(decision.retry_after)
            if response is None:
                response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            if decision is not None:
                response.headers["X-RateLimit-Limit"] = str(decision.limit)
                response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
            return response
        except Exception:
            logger.exception(
                "http request failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            raise
        finally:
            logger.info(
                "http request completed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": max(0, int((perf_counter() - started) * 1000)),
                },
            )
            REQUEST_ID_CONTEXT.reset(context_token)
