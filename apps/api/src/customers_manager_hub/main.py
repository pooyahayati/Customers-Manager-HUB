from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx2
from fastapi import FastAPI

from customers_manager_hub.agents import prompt_router
from customers_manager_hub.agents import router as agents_router
from customers_manager_hub.ai_gateway import AIGateway
from customers_manager_hub.ai_profiles import router as ai_profiles_router
from customers_manager_hub.ai_providers import build_live_provider_registry
from customers_manager_hub.auth import router as auth_router
from customers_manager_hub.channel_gateway import ChannelRegistry
from customers_manager_hub.channel_queue import ChannelJobQueue, create_channel_redis
from customers_manager_hub.channel_webhooks import router as channel_webhooks_router
from customers_manager_hub.channels import router as channels_router
from customers_manager_hub.config import Settings, get_settings
from customers_manager_hub.contacts import router as contacts_router
from customers_manager_hub.conversations import router as conversations_router
from customers_manager_hub.database import create_database
from customers_manager_hub.health import router as health_router
from customers_manager_hub.logging_config import configure_logging
from customers_manager_hub.telegram import TelegramAdapter
from customers_manager_hub.tenants import router as tenants_router
from customers_manager_hub.website import WebsiteAdapter
from customers_manager_hub.website_chat import admin_router as website_admin_router
from customers_manager_hub.website_chat import public_router as website_public_router


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI application without product-domain side effects."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.app_log_level)
    engine, session_factory = create_database(resolved_settings)
    channel_redis = create_channel_redis(resolved_settings)
    channel_queue = ChannelJobQueue(channel_redis)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
        try:
            async with httpx2.AsyncClient() as external_http_client:
                ai_provider_registry = build_live_provider_registry(
                    resolved_settings,
                    external_http_client,
                )
                channel_registry = ChannelRegistry(
                    (TelegramAdapter(external_http_client), WebsiteAdapter())
                )
                application.state.ai_gateway = AIGateway(ai_provider_registry, session_factory)
                application.state.channel_registry = channel_registry
                application.state.channel_queue = channel_queue
                yield
        finally:
            await channel_redis.aclose()
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
    application.include_router(ai_profiles_router)
    application.include_router(prompt_router)
    application.include_router(agents_router)
    application.include_router(website_admin_router)
    application.include_router(channels_router)
    application.include_router(contacts_router)
    application.include_router(conversations_router)
    application.include_router(channel_webhooks_router)
    application.include_router(website_public_router)
    return application


app = create_app()
