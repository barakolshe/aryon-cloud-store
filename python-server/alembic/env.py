"""Alembic's runtime environment.

Wired to the same two things the application uses: `settings.database_url` from
app/config.py, so the environment is still read in exactly one place, and `Base.metadata`,
so autogenerate diffs against the models rather than against a hand-kept copy of them.

The engine is async because the driver is: `postgresql+psycopg://` resolves to psycopg's
async dialect, which cannot be driven from a plain `create_engine`. Migrations themselves
are ordinary synchronous code, so the connection is handed to `run_sync`.
"""
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from app.config import settings
from app.models.base import Base

# Imported for the side effect of registering their tables on Base.metadata. A model that
# is not reachable from here is invisible to autogenerate, which then cheerfully writes a
# migration dropping its table. Every new model module gets added to this line.
from app.models import node, node_closure  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit the migration SQL without connecting to anything.

    `alembic upgrade head --sql` lands here. Useful for review, and it is how the tests
    assert on the generated DDL without a database.
    """
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open one connection and run the pending migrations over it."""
    configuration = config.get_section(config.config_ini_section, {})
    # Set on the section dict rather than through config.set_main_option: that path runs
    # the value through configparser interpolation, where a '%' in a password raises.
    configuration['sqlalchemy.url'] = settings.database_url

    # NullPool: this process opens one connection, runs the migrations and exits, so a
    # pool would only add idle connections for Postgres to keep.
    connectable = async_engine_from_config(
        configuration,
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
