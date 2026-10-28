"""
Alembic's environment: runs every migration as the owner role.

It reads only `MigrationSettings`, so the owner's credentials never come from
the services' settings. Each session sets the lock timeout, and no statement
timeout, as connection settings, so both hold inside autocommit blocks too.
"""

import asyncio
from logging.config import fileConfig
from typing import TYPE_CHECKING

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tally_aloft.adapters.outbound.postgres.engine import connect_args
from tally_aloft.adapters.outbound.postgres.tables import load_metadata
from tally_aloft.bootstrap.settings import MigrationSettings, load_migration_settings

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

config = context.config


def _settings() -> MigrationSettings:
    """Return the settings a caller passed in, or read them from the environment."""
    passed: object = config.attributes.get("migration_settings")
    if isinstance(passed, MigrationSettings):
        return passed
    return load_migration_settings()


def _run_migrations(connection: Connection) -> None:
    """Run the pending migrations on `connection`, one transaction each."""
    # An autocommit block commits the transaction before it, so each revision
    # gets its own transaction, as Alembic recommends for them.
    context.configure(
        connection=connection,
        target_metadata=load_metadata(),
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online(settings: MigrationSettings) -> None:
    """Connect as the owner role and run the migrations."""
    engine = create_async_engine(
        settings.migration_database_url.get_secret_value(),
        poolclass=NullPool,
        hide_parameters=True,
        connect_args=connect_args(
            {
                "lock_timeout": f"{settings.migration_lock_timeout}s",
                # Overrides any database or role default, which could cut a
                # long index build or backfill off.
                "statement_timeout": "0",
            }
        ),
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    msg = "Offline mode (--sql) isn't supported; run migrations against a database."
    raise SystemExit(msg)
migration_settings = _settings()
# A caller that runs Alembic in its own process, such as the tests, owns logging.
configure_logging = config.attributes.get("configure_logging") is not False
if configure_logging and config.config_file_name is not None:
    fileConfig(config.config_file_name)
asyncio.run(_run_online(migration_settings))
