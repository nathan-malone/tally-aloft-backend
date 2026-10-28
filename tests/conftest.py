import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest

from tests.support.migrations import alembic_config, upgrade
from tests.support.postgres import (
    PostgresServer,
    running_postgres,
    temporary_database,
    truncate_tables,
)
from tests.support.tables import create_test_tables

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterator


@pytest.fixture(scope="session")
def postgres_server() -> Iterator[PostgresServer]:
    """
    Start one Postgres per test worker, on first use only, and migrate it.

    The test-only tables are created after the migrations, as the owner. Tests
    that use it need Docker, so they're marked `container`.
    """
    with running_postgres() as server:
        # env.py calls asyncio.run, which would replace this thread's event
        # loop under pytest-asyncio, so the setup runs on a thread of its own.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(upgrade, alembic_config(server.owner_url)).result()
            pool.submit(asyncio.run, create_test_tables(server.owner_url)).result()
        yield server


@pytest.fixture
async def database(postgres_server: PostgresServer) -> AsyncGenerator[PostgresServer]:
    """The shared database, with every table emptied after the test."""
    yield postgres_server
    await truncate_tables(postgres_server)


@pytest.fixture
async def fresh_database(
    postgres_server: PostgresServer,
) -> AsyncGenerator[PostgresServer]:
    """An empty database owned by the owner role, with no migrations applied."""
    async with temporary_database(postgres_server) as server:
        yield server
