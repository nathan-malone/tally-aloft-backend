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
class PostgresServer:
    """Where a test Postgres listens, and the URL of each role on a database."""

    host: str
    port: int
    database: str = DATABASE

    def url(self, role: str) -> str:
        """Return `role`'s asyncpg URL, with the local password the init script set."""
        password = SUPERUSER if role == SUPERUSER else role
        return URL.create(
            "postgresql+asyncpg",
            username=role,
            password=password,
            host=self.host,
            port=self.port,
            database=self.database,
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

    def on(self, database: str) -> PostgresServer:
        """Return this server with `database` in place of the default one."""
        return replace(self, database=database)


@contextmanager
def running_postgres() -> Generator[PostgresServer]:
    """Start Postgres with the Compose init script, and stop it on exit."""
    container = PostgresContainer(
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
    with container:
        yield PostgresServer(
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


async def execute(url: str, statement: str) -> None:
    """Run one statement on its own connection, and commit it."""
    async with connected(url) as connection:
        _ = await connection.execute(text(statement))
        await connection.commit()


@asynccontextmanager
async def temporary_database(server: PostgresServer) -> AsyncGenerator[PostgresServer]:
    """
    Create an empty database owned by the owner role, and drop it on exit.

    Tests that change the schema or the migration state run on one, so they
    never touch the shared database.
    """
    name = f"test_{uuid.uuid4().hex}"
    await execute_as_superuser(server, f'CREATE DATABASE "{name}" OWNER {OWNER_ROLE}')
    try:
        yield server.on(name)
    finally:
        await execute_as_superuser(server, f'DROP DATABASE "{name}" WITH (FORCE)')


async def execute_as_superuser(server: PostgresServer, statement: str) -> None:
    """Run a statement that can't run in a transaction, such as `CREATE DATABASE`."""
    async with connected(server.superuser_url, autocommit=True) as connection:
        _ = await connection.execute(text(statement))


async def truncate_tables(server: PostgresServer, *, lock_timeout: int = 5) -> None:
    """
    Empty every table but Alembic's, through an owner connection of its own.

    A test that leaves a transaction open on a table makes this fail with a
    lock timeout rather than hang the run.
    """
    async with connected(server.owner_url) as connection:
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
