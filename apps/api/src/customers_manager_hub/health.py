import asyncio
import logging
from typing import Literal

import psycopg
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import RedisError

from customers_manager_hub.config import Settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])

DependencyStatus = Literal["ok", "unavailable"]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessDependencies(BaseModel):
    postgres: DependencyStatus
    redis: DependencyStatus


class ReadinessResponse(BaseModel):
    status: Literal["ok", "not_ready"]
    dependencies: ReadinessDependencies


async def check_postgres(settings: Settings) -> bool:
    """Check PostgreSQL reachability and pgvector extension availability."""
    try:
        async with (
            await psycopg.AsyncConnection.connect(
                settings.postgres_dsn,
                connect_timeout=settings.dependency_timeout_seconds,
                autocommit=True,
            ) as connection,
            connection.cursor() as cursor,
        ):
            await cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector')"
            )
            row = await cursor.fetchone()
            return bool(row is not None and row[0])
    except psycopg.Error, OSError, TimeoutError:
        logger.warning("PostgreSQL readiness check failed")
        return False


async def check_redis(settings: Settings) -> bool:
    """Check Redis reachability without retaining state."""
    client = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.dependency_timeout_seconds,
        socket_timeout=settings.dependency_timeout_seconds,
        decode_responses=True,
    )
    try:
        return bool(await client.ping())
    except RedisError, OSError, TimeoutError:
        logger.warning("Redis readiness check failed")
        return False
    finally:
        await client.aclose()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Process liveness endpoint; intentionally independent of external services."""
    return HealthResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def ready(request: Request) -> ReadinessResponse | JSONResponse:
    """Dependency readiness endpoint for PostgreSQL and Redis."""
    settings: Settings = request.app.state.settings
    postgres_ok, redis_ok = await asyncio.gather(
        check_postgres(settings),
        check_redis(settings),
    )

    response = ReadinessResponse(
        status="ok" if postgres_ok and redis_ok else "not_ready",
        dependencies=ReadinessDependencies(
            postgres="ok" if postgres_ok else "unavailable",
            redis="ok" if redis_ok else "unavailable",
        ),
    )

    if not (postgres_ok and redis_ok):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response.model_dump(mode="json"),
        )
    return response
