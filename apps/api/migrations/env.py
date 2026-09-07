from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from customers_manager_hub import agent_models as _agent_models  # noqa: F401
from customers_manager_hub import ai_models as _ai_models  # noqa: F401
from customers_manager_hub import analytics_models as _analytics_models  # noqa: F401
from customers_manager_hub import channel_models as _channel_models  # noqa: F401
from customers_manager_hub import handoff_models as _handoff_models  # noqa: F401
from customers_manager_hub import knowledge_models as _knowledge_models  # noqa: F401
from customers_manager_hub import memory_models as _memory_models  # noqa: F401
from customers_manager_hub import policy_models as _policy_models  # noqa: F401
from customers_manager_hub import tool_models as _tool_models  # noqa: F401
from customers_manager_hub.config import get_settings
from customers_manager_hub.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().sqlalchemy_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        get_settings().sqlalchemy_database_url,
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
