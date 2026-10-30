"""
Real Postgres for the tests, in a testcontainers container.

The container runs Compose's own init script, so its roles match local
development's. Tests connect as the application role, and the owner role runs
the migrations and the clean-up.
"""

import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

from tests.support.containers import started
from tests.support.paths import ROOT

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator

    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import AsyncConnection

# The one place the tests' Postgres version is set; compose.yaml must match it.
POSTGRES_IMAGE = "postgres:18"
DATABASE = "tally_aloft"
SUPERUSER = "postgres"
OWNER_ROLE = "tally_aloft_owner"
APP_ROLE = "tally_aloft_app"
INIT_SCRIPT = ROOT / "compose" / "postgres-init.sql"
_PORT = 5432

# SQLSTATE codes the tests expect.
INSUFFICIENT_PRIVILEGE = "42501"
QUERY_CANCELED = "57014"
LOCK_NOT_AVAILABLE = "55P03"


@dataclass(frozen=True, slots=True)
class PostgresDatabase:
    """One database on a test Postgres, and each role's URL for it."""

    host: str
    port: int
    name: str = DATABASE

    def url(self, role: str) -> str:
        """
        Return `role`'s asyncpg URL for this database.

        Every local password equals its role's name, as the container's
        environment and `compose/postgres-init.sql` set them.
        """
        return URL.create(
            "postgresql+asyncpg",
            username=role,
            password=role,
            host=self.host,
            port=self.port,
            database=self.name,
        ).render_as_string(hide_password=False)

    @property
    def superuser_url(self) -> str:
        """Return the bootstrap superuser's URL, for creating databases."""
        return self.url(SUPERUSER)

    @property
    def owner_url(self) -> str:
        """Return the owner role's URL, which migrations use."""
        return self.url(OWNER_ROLE)

    @property
    def app_url(self) -> str:
        """Return the application role's URL, which tests connect with."""
        return self.url(APP_ROLE)

    def on(self, name: str) -> PostgresDatabase:
        """Return the database called `name` on the same server."""
        return replace(self, name=name)


@contextmanager
def running_postgres() -> Generator[PostgresDatabase]:
    """
    Start Postgres with the Compose init script, and stop it on exit.

    It yields the default database, which the session's migrations run on.
    """

    def make() -> PostgresContainer:
        return PostgresContainer(
            POSTGRES_IMAGE,
            username=SUPERUSER,
            password=SUPERUSER,
            dbname=DATABASE,
            driver=None,
        ).with_volume_mapping(
            str(INIT_SCRIPT), "/docker-entrypoint-initdb.d/init.sql", "ro"
        )

    # testcontainers waits with psql over TCP, which the image's initialization
    # server doesn't listen on, so the init script has run by the time it's up.
    with started(make) as container:
        yield PostgresDatabase(
            host=container.get_container_host_ip(),
            port=container.get_exposed_port(_PORT),
        )


@asynccontextmanager
async def connected(
    url: str, *, autocommit: bool = False
) -> AsyncGenerator[AsyncConnection]:
    """Yield one connection to `url`, outside any pool, and close it on exit."""
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT" if autocommit else "READ COMMITTED",
    )
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


async def execute(url: str, statement: str, *, autocommit: bool = False) -> None:
    """
    Run one statement on its own connection, and commit it.

    `autocommit` runs it outside any transaction, as `CREATE DATABASE` needs.
    """
    async with connected(url, autocommit=autocommit) as connection:
        _ = await connection.execute(text(statement))
        await connection.commit()


@asynccontextmanager
async def temporary_database(
    database: PostgresDatabase,
) -> AsyncGenerator[PostgresDatabase]:
    """
    Create an empty database owned by the owner role, and drop it on exit.

    It's on the same server as `database`. Tests that change the schema or
    the migration state run on one, so they never touch the shared database.
    """
    name = f"test_{uuid.uuid4().hex}"
    create = f'CREATE DATABASE "{name}" OWNER {OWNER_ROLE}'
    await execute(database.superuser_url, create, autocommit=True)
    try:
        yield database.on(name)
    finally:
        drop = f'DROP DATABASE "{name}" WITH (FORCE)'
        await execute(database.superuser_url, drop, autocommit=True)


async def truncate_tables(database: PostgresDatabase, *, lock_timeout: int = 5) -> None:
    """
    Empty every table but Alembic's, through an owner connection of its own.

    A test that leaves a transaction open on a table makes this fail with a
    lock timeout rather than hang the run.
    """
    async with connected(database.owner_url) as connection:
        _ = await connection.execute(text(f"SET lock_timeout = '{lock_timeout}s'"))
        result = await connection.execute(
            text(
                "SELECT format('%I.%I', schemaname, tablename) FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        )
        tables = ", ".join(result.scalars().all())
        if tables != "":
            truncate = f"TRUNCATE {tables} RESTART IDENTITY CASCADE"
            _ = await connection.execute(text(truncate))
        await connection.commit()


def sqlstate(error: DBAPIError) -> str | None:
    """Return the SQLSTATE that SQLAlchemy's asyncpg adapter puts on the error."""
    code: object = getattr(error.orig, "sqlstate", None)
    return code if isinstance(code, str) else None
