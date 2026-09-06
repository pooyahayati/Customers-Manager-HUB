from collections.abc import AsyncIterator
from typing import cast

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from customers_manager_hub.config import Settings

type AsyncSessionFactory = async_sessionmaker[AsyncSession]


def create_database(settings: Settings) -> tuple[AsyncEngine, AsyncSessionFactory]:
    """Create the application engine and session factory without connecting eagerly."""
    engine = create_async_engine(
        settings.sqlalchemy_database_url,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield one request-scoped database session."""
    session_factory = cast(AsyncSessionFactory, request.app.state.db_session_factory)
    async with session_factory() as session:
        yield session
