"""Alembic environment.

The database URL comes from Anchor's own settings rather than ``alembic.ini``,
so there is exactly one place a connection string is configured and the
migrations cannot be pointed at a different database than the application.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from agent.config import get_settings
from agent.db.base import Base

# Importing the models module is what registers every table on Base.metadata.
# Without it, autogenerate would see an empty schema and propose dropping
# everything.
import agent.db.models  # noqa: F401  isort:skip

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_url = get_settings().DATABASE_URL
if not _url:
    raise RuntimeError(
        "DATABASE_URL is not set, so there is no database to migrate. "
        "Set it in .env and try again."
    )
config.set_main_option("sqlalchemy.url", _url)


def run_migrations_offline() -> None:
    """Emit SQL without connecting. Used to review a migration before applying."""
    context.configure(
        url=_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # SQLite cannot ALTER most things in place, so batch mode rewrites
            # the table instead. It is a no-op on PostgreSQL.
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
