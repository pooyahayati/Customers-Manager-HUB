from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from customers_manager_hub.auth import router as auth_router
from customers_manager_hub.config import Settings, get_settings
from customers_manager_hub.database import create_database
from customers_manager_hub.health import router as health_router
from customers_manager_hub.logging_config import configure_logging
from customers_manager_hub.tenants import router as tenants_router


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI application without product-domain side effects."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.app_log_level)
    engine, session_factory = create_database(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        try:
            yield
        finally:
            await engine.dispose()

    application = FastAPI(
        title=resolved_settings.app_name,
        debug=resolved_settings.app_debug,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.db_session_factory = session_factory
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(tenants_router)
    return application


app = create_app()
