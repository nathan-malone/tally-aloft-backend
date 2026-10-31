import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest

from tally_aloft.adapters.outbound.redis.client import build_client
from tests.support.migrations import alembic_config, upgrade
from tests.support.postgres import (
    PostgresDatabase,
    running_postgres,
    temporary_database,
    truncate_tables,
)
from tests.support.tables import create_test_tables
from tests.support.valkey import ValkeyServer, flush, running_valkey

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterator

    from redis.asyncio import Redis


@pytest.fixture(scope="session")
def postgres_server() -> Iterator[PostgresDatabase]:
    """
    Start one Postgres per test worker, on first use only, and migrate it.

    It yields the shared database. Use it only to read, or for statements that
    fail: nothing empties it, so a test that writes takes `database` instead.
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
async def database(
    postgres_server: PostgresDatabase,
) -> AsyncGenerator[PostgresDatabase]:
    """The shared database, for tests that write: every table is emptied after."""
    yield postgres_server
    await truncate_tables(postgres_server)


@pytest.fixture
async def fresh_database(
    postgres_server: PostgresDatabase,
) -> AsyncGenerator[PostgresDatabase]:
    """An empty database owned by the owner role, with no migrations applied."""
    async with temporary_database(postgres_server) as fresh:
        yield fresh


@pytest.fixture(scope="session")
def valkey_server() -> Iterator[ValkeyServer]:
    """
    Start one Valkey per test worker, on first use only.

    Tests that write take `valkey_url` instead, which empties it after each test.
    Tests that use it need Docker, so they're marked `container`.
    """
    with running_valkey() as server:
        yield server


@pytest.fixture
async def valkey_url(valkey_server: ValkeyServer) -> AsyncGenerator[str]:
    """The shared Valkey's URL: every key is deleted after the test."""
    yield valkey_server.url
    await flush(valkey_server.url)


@pytest.fixture
async def valkey(valkey_url: str) -> AsyncGenerator[Redis]:
    """A client on the shared Valkey, for reading what a test left there."""
    client = build_client(valkey_url)
    yield client
    await client.aclose()
