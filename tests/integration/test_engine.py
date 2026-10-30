import time
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from tally_aloft.adapters.outbound.postgres.engine import (
    CONNECT_TIMEOUT_SECONDS,
    POOL_TIMEOUT_SECONDS,
)
from tally_aloft.bootstrap.resources import open_resources
from tally_aloft.bootstrap.settings import Settings
from tests.support.postgres import QUERY_CANCELED, sqlstate
from tests.support.server import silent_server

if TYPE_CHECKING:
    from tests.support.postgres import PostgresDatabase

MARKER = "secret-marker"
MISSING_TABLE_QUERY = text("SELECT * FROM missing WHERE x = :marker")


@pytest.mark.container
async def test_errors_hide_statement_parameters(
    postgres_server: PostgresDatabase,
) -> None:
    settings = Settings.for_tests(database_url=postgres_server.app_url)

    async with open_resources(settings) as resources:
        with pytest.raises(DBAPIError) as error:
            async with resources.engine.connect() as connection:
                _ = await connection.execute(MISSING_TABLE_QUERY, {"marker": MARKER})

    assert "[parameters:" not in str(error.value)
    assert MARKER not in str(error.value)


@pytest.mark.container
async def test_an_engine_without_hidden_parameters_would_show_them(
    postgres_server: PostgresDatabase,
) -> None:
    # The control for the test above: the marker does reach the message here.
    engine = create_async_engine(postgres_server.app_url)
    try:
        with pytest.raises(DBAPIError) as error:
            async with engine.connect() as connection:
                _ = await connection.execute(MISSING_TABLE_QUERY, {"marker": MARKER})
    finally:
        await engine.dispose()

    assert MARKER in str(error.value)


@pytest.mark.container
async def test_statement_timeout_cancels_a_long_statement(
    postgres_server: PostgresDatabase,
) -> None:
    settings = Settings.for_tests(
        database_url=postgres_server.app_url, database_statement_timeout=1
    )

    async with open_resources(settings) as resources:
        with pytest.raises(DBAPIError) as error:
            async with resources.engine.connect() as connection:
                _ = await connection.execute(text("SELECT pg_sleep(3)"))

    assert sqlstate(error.value) == QUERY_CANCELED


@pytest.mark.container
async def test_statement_timeout_defaults_to_30_seconds(
    postgres_server: PostgresDatabase,
) -> None:
    settings = Settings.for_tests(database_url=postgres_server.app_url)

    async with (
        open_resources(settings) as resources,
        resources.engine.connect() as connection,
    ):
        result = await connection.execute(text("SHOW statement_timeout"))

    assert result.scalar_one() == "30s"


async def test_engine_follows_the_pool_settings() -> None:
    settings = Settings.for_tests(database_pool_size=3, database_max_overflow=2)

    async with open_resources(settings) as resources:
        pool = resources.engine.pool
        hides_parameters = resources.engine.sync_engine.hide_parameters

    assert isinstance(pool, AsyncAdaptedQueuePool)
    assert pool.size() == 3
    assert pool._max_overflow == 2  # noqa: SLF001  # The pool has no public getter.
    assert pool.timeout() == POOL_TIMEOUT_SECONDS
    assert hides_parameters


async def test_connecting_to_a_server_that_never_answers_times_out() -> None:
    async with silent_server() as port:
        settings = Settings.for_tests(
            database_url=f"postgresql+asyncpg://app@127.0.0.1:{port}/tally"
        )
        async with open_resources(settings) as resources:
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                async with resources.engine.connect():
                    pass

    assert time.monotonic() - started < CONNECT_TIMEOUT_SECONDS + 2
