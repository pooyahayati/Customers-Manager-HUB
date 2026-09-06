import asyncio
import logging
import os
import signal
import socket
from time import monotonic

import httpx2

from customers_manager_hub.channel_gateway import ChannelProviderError, ChannelRegistry
from customers_manager_hub.channel_queue import ChannelJob, ChannelJobQueue, create_channel_redis
from customers_manager_hub.channel_runtime import (
    ChannelRuntimeError,
    mark_event_ignored,
    process_channel_event,
)
from customers_manager_hub.config import Settings, get_settings
from customers_manager_hub.database import create_database
from customers_manager_hub.health import check_postgres, check_redis
from customers_manager_hub.logging_config import configure_logging
from customers_manager_hub.telegram import TelegramAdapter

logger = logging.getLogger(__name__)
_RECLAIM_INTERVAL_SECONDS = 5.0


async def dependencies_ready(settings: Settings) -> bool:
    """Return whether the worker's required runtime dependencies are reachable."""
    postgres_ok, redis_ok = await asyncio.gather(
        check_postgres(settings),
        check_redis(settings),
    )
    return postgres_ok and redis_ok


async def _process_job(
    job: ChannelJob,
    queue: ChannelJobQueue,
    registry: ChannelRegistry,
    settings: Settings,
    session_factory: object,
) -> None:
    from customers_manager_hub.database import AsyncSessionFactory

    typed_session_factory = session_factory
    assert isinstance(typed_session_factory, AsyncSessionFactory)
    try:
        await process_channel_event(typed_session_factory, registry, settings, job.event_id)
    except ChannelProviderError as exc:
        logger.warning(
            "channel provider job failed",
            extra={"event_id": str(job.event_id), "error_code": exc.code},
        )
        if exc.retryable:
            return
        await mark_event_ignored(typed_session_factory, job.event_id, exc.code)
    except ChannelRuntimeError as exc:
        logger.error(
            "channel runtime job rejected",
            extra={"event_id": str(job.event_id), "error_code": exc.code},
        )
        await mark_event_ignored(typed_session_factory, job.event_id, exc.code)
    except Exception:
        logger.exception("channel job failed", extra={"event_id": str(job.event_id)})
        return
    await queue.acknowledge(job.stream_id)


async def run_worker_async(settings: Settings, stop_event: asyncio.Event | None = None) -> None:
    if not await dependencies_ready(settings):
        logger.error("worker dependencies unavailable")
        raise SystemExit(1)

    engine, session_factory = create_database(settings)
    redis_client = create_channel_redis(settings)
    queue = ChannelJobQueue(redis_client)
    await queue.ensure_group()
    consumer_name = f"{socket.gethostname()}-{os.getpid()}"
    resolved_stop_event = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()

    if stop_event is None:
        for watched_signal in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                watched_signal,
                lambda signal_number=watched_signal: (
                    logger.info(
                        "worker shutdown requested",
                        extra={"signal": int(signal_number)},
                    ),
                    resolved_stop_event.set(),
                ),
            )

    try:
        async with httpx2.AsyncClient() as external_http_client:
            registry = ChannelRegistry((TelegramAdapter(external_http_client),))
            logger.info("worker started", extra={"consumer": consumer_name})
            last_reclaim = 0.0
            while not resolved_stop_event.is_set():
                jobs: list[ChannelJob] = []
                now = monotonic()
                if now - last_reclaim >= _RECLAIM_INTERVAL_SECONDS:
                    jobs.extend(await queue.reclaim(consumer_name))
                    last_reclaim = now
                if not jobs:
                    jobs.extend(await queue.consume(consumer_name, block_ms=1_000))
                for job in jobs:
                    if resolved_stop_event.is_set():
                        break
                    await _process_job(
                        job,
                        queue,
                        registry,
                        settings,
                        session_factory,
                    )
    finally:
        await redis_client.aclose()
        await engine.dispose()
        logger.info("worker stopped")


def run_worker(settings: Settings | None = None) -> None:
    """Run the worker lifecycle until a shutdown signal is received."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.app_log_level)
    asyncio.run(run_worker_async(resolved_settings))


def main() -> None:
    """CLI entry point for ``python -m customers_manager_hub.worker``."""
    run_worker()


if __name__ == "__main__":
    main()
