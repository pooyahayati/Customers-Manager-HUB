import asyncio
import logging
import signal
from threading import Event
from types import FrameType

from customers_manager_hub.config import Settings, get_settings
from customers_manager_hub.health import check_postgres, check_redis
from customers_manager_hub.logging_config import configure_logging

logger = logging.getLogger(__name__)


async def dependencies_ready(settings: Settings) -> bool:
    """Return whether the worker's required runtime dependencies are reachable."""
    postgres_ok, redis_ok = await asyncio.gather(
        check_postgres(settings),
        check_redis(settings),
    )
    return postgres_ok and redis_ok


def run_worker(settings: Settings | None = None) -> None:
    """Run the minimal worker lifecycle until a shutdown signal is received."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.app_log_level)

    if not asyncio.run(dependencies_ready(resolved_settings)):
        logger.error("worker dependencies unavailable")
        raise SystemExit(1)

    stop_event = Event()

    def request_shutdown(signum: int, frame: FrameType | None) -> None:
        del frame
        logger.info("worker shutdown requested", extra={"signal": signum})
        stop_event.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    logger.info("worker started")
    stop_event.wait()
    logger.info("worker stopped")


def main() -> None:
    """CLI entry point for ``python -m customers_manager_hub.worker``."""
    run_worker()


if __name__ == "__main__":
    main()
