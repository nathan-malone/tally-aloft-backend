import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tally_aloft.adapters.outbound.postgres.unit_of_work import (
    PostgresUnitOfWork,
    unit_of_work_factory,
)
from tally_aloft.bootstrap.resources import open_resources
from tally_aloft.bootstrap.settings import Settings
from tests.support.postgres import connected
from tests.support.tables import test_rows

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from tally_aloft.application.shared.unit_of_work import SharedUnitOfWork
    from tests.support.postgres import PostgresServer

# Pyrefly fails here if the adapter drifts from the Protocol use cases hold.
_unit_of_work: SharedUnitOfWork = PostgresUnitOfWork(async_sessionmaker())


@pytest.fixture
async def make_unit_of_work(
    database: PostgresServer,
) -> AsyncGenerator[Callable[[], PostgresUnitOfWork]]:
    settings = Settings.for_tests(database_url=database.app_url)
    async with open_resources(settings) as resources:
        yield unit_of_work_factory(resources.engine)


async def _committed_values(database: PostgresServer) -> list[str]:
    """Read `test_rows` on a connection of its own, which sees only commits."""
    async with connected(database.app_url) as connection:
        result = await connection.execute(select(test_rows.c.value))
        return list(result.scalars().all())


async def _insert(unit_of_work: PostgresUnitOfWork, value: str) -> None:
    _ = await unit_of_work.session.execute(insert(test_rows).values(value=value))


@pytest.mark.container
async def test_a_clean_exit_commits(
    make_unit_of_work: Callable[[], PostgresUnitOfWork], database: PostgresServer
) -> None:
    async with make_unit_of_work() as uow:
        await _insert(uow, "kept")

    assert await _committed_values(database) == ["kept"]


@pytest.mark.container
async def test_an_exception_rolls_back(
    make_unit_of_work: Callable[[], PostgresUnitOfWork], database: PostgresServer
) -> None:
    msg = "the use case failed"

    async def failing_use_case() -> None:
        async with make_unit_of_work() as uow:
            await _insert(uow, "dropped")
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match=msg):
        await failing_use_case()

    assert await _committed_values(database) == []


@pytest.mark.container
async def test_cancellation_rolls_back(
    make_unit_of_work: Callable[[], PostgresUnitOfWork], database: PostgresServer
) -> None:
    inserted = asyncio.Event()

    async def body() -> None:
        async with make_unit_of_work() as uow:
            await _insert(uow, "dropped")
            inserted.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(body())
    _ = await inserted.wait()
    _ = task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await _committed_values(database) == []


@pytest.mark.container
async def test_each_unit_of_work_has_its_own_session(
    make_unit_of_work: Callable[[], PostgresUnitOfWork],
) -> None:
    async with make_unit_of_work() as first, make_unit_of_work() as second:
        assert first.session is not second.session


@pytest.mark.container
async def test_a_unit_of_work_is_entered_once(
    make_unit_of_work: Callable[[], PostgresUnitOfWork],
) -> None:
    uow = make_unit_of_work()

    async with uow:
        with pytest.raises(RuntimeError):
            async with uow:
                pass
    with pytest.raises(RuntimeError):
        async with uow:
            pass


def test_the_session_exists_only_inside_async_with() -> None:
    with pytest.raises(RuntimeError):
        _ = PostgresUnitOfWork(async_sessionmaker()).session
