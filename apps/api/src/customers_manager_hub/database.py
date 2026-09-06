from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from customers_manager_hub.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Create the shared async SQLAlchemy engine lazily."""
    settings = get_settings()
    return create_async_engine(
        settings.sqlalchemy_database_url,
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the shared async session factory."""
    return async_sessionmaker(
        get_engine(),
        expire_on_commit=False,
    )


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield one request-scoped database session."""
    async with get_session_factory()() as session:
        yield session
