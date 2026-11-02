"""
`run_once` on real Postgres: replays, and repeats that wait on a first call.

The concurrency tests never sleep for a fixed time. A held first call keeps
its transaction open on an event, and a coordinator watches Postgres until
the repeats are waiting on its key row's lock, then releases it.
"""

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select, text

from tally_aloft.adapters.outbound.postgres.engine import build_engine
from tally_aloft.adapters.outbound.postgres.tables.idempotency_keys import (
    IdempotencyKeyRecord,
)
from tally_aloft.adapters.outbound.postgres.tables.jobs import JobRecord
from tally_aloft.adapters.outbound.postgres.unit_of_work import unit_of_work_factory
from tally_aloft.application.shared.errors import IdempotencyKeyReusedError
from tally_aloft.application.shared.idempotency import IdempotencyRequest, run_once
from tally_aloft.domain.shared.ids import PilotId
from tests.support.idempotency import (
    OPERATION,
    RECEIPT_CODEC,
    IdempotentWrite,
    record_receipt,
    write_command,
)
from tests.support.in_process_worker import eventually
from tests.support.postgres import APP_ROLE, connected

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine

    from sqlalchemy.ext.asyncio import AsyncEngine

    from tally_aloft.adapters.outbound.postgres.tables import Base
    from tally_aloft.application.shared.unit_of_work import SharedUnitOfWork
    from tests.support.idempotency import WriteReceipt
    from tests.support.postgres import PostgresDatabase

pytestmark = pytest.mark.container

CALLS = 20
# Room for every call's connection, and one more (NFR-10).
POOL_SIZE = CALLS + 1
# Short, so a repeat stuck on a lock fails inside the test.
STATEMENT_TIMEOUT_SECONDS = 15
WAITERS_DEADLINE_SECONDS = 10
TEST_DEADLINE_SECONDS = 30


@pytest.fixture
async def engine(database: PostgresDatabase) -> AsyncGenerator[AsyncEngine]:
    engine = build_engine(
        database.app_url,
        pool_size=POOL_SIZE,
        max_overflow=0,
        statement_timeout_seconds=STATEMENT_TIMEOUT_SECONDS,
    )
    yield engine
    await engine.dispose()


@pytest.fixture
def uow(engine: AsyncEngine) -> Callable[[], SharedUnitOfWork]:
    return unit_of_work_factory(engine)


async def _count(database: PostgresDatabase, table: type[Base]) -> int:
    async with connected(database.app_url) as connection:
        statement = select(func.count()).select_from(table)
        return (await connection.execute(statement)).scalar_one()


async def test_a_first_call_writes_once_and_a_repeat_replays_it(
    uow: Callable[[], SharedUnitOfWork], database: PostgresDatabase
) -> None:
    write = IdempotentWrite(uow)
    command = write_command()

    first = await write(command)
    repeat = await write(command)

    assert repeat == first
    assert await _count(database, JobRecord) == 1
    assert await _count(database, IdempotencyKeyRecord) == 1


async def test_a_repeat_with_another_hash_is_refused(
    uow: Callable[[], SharedUnitOfWork], database: PostgresDatabase
) -> None:
    write = IdempotentWrite(uow)
    command = write_command()
    _ = await write(command)

    with pytest.raises(IdempotencyKeyReusedError):
        _ = await write(write_command(command.actor, request_hash="hash-2"))

    assert await _count(database, JobRecord) == 1


async def test_a_failed_write_keeps_nothing_and_a_retry_runs(
    uow: Callable[[], SharedUnitOfWork], database: PostgresDatabase
) -> None:
    write = IdempotentWrite(uow)
    command = write_command(fail=True)

    with pytest.raises(RuntimeError, match="the test write failed"):
        _ = await write(command)

    assert await _count(database, JobRecord) == 0
    assert await _count(database, IdempotencyKeyRecord) == 0
    _ = await write(write_command(command.actor))
    assert await _count(database, JobRecord) == 1


async def test_two_actors_with_one_key_each_write_once(
    uow: Callable[[], SharedUnitOfWork], database: PostgresDatabase
) -> None:
    write = IdempotentWrite(uow)

    first = await write(write_command())
    second = await write(write_command())

    assert first != second
    assert await _count(database, JobRecord) == 2
    assert await _count(database, IdempotencyKeyRecord) == 2


@dataclass
class HeldWrite:
    """
    Writes as `IdempotentWrite` does, but holds its transaction open.

    Each run of the action records its job, then waits for `release`. With
    `fail_first`, the first run then raises, so its transaction rolls back.
    """

    uow: Callable[[], SharedUnitOfWork]
    fail_first: bool = False
    runs: int = 0
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def __call__(self, request: IdempotencyRequest) -> WriteReceipt:
        async with self.uow() as uow:

            async def action() -> WriteReceipt:
                receipt = await record_receipt(uow, request.actor, "held")
                self.runs += 1
                self.entered.set()
                await self.release.wait()
                if self.fail_first and self.runs == 1:
                    msg = "the held write failed"
                    raise RuntimeError(msg)
                return receipt

            return await run_once(uow, request, action, RECEIPT_CODEC)


async def _lock_waiters(database: PostgresDatabase) -> int:
    """Return how many of the application role's backends wait on a lock."""
    statement = text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND usename = :role "
        "AND wait_event_type = 'Lock'"
    )
    async with connected(database.app_url) as connection:
        result = await connection.execute(statement, {"role": APP_ROLE})
        return result.scalar_one()


async def _wait_for_lock_waiters(database: PostgresDatabase, count: int) -> None:
    """Return once `count` backends wait on a lock, or fail after a deadline."""

    async def waiting() -> bool:
        return await _lock_waiters(database) >= count

    await eventually(waiting, within=WAITERS_DEADLINE_SECONDS)


async def _together(
    held: HeldWrite,
    calls: list[Coroutine[object, object, WriteReceipt]],
    coordinate: Callable[[list[asyncio.Task[WriteReceipt]]], Awaitable[None]],
) -> list[WriteReceipt | BaseException]:
    """
    Run `calls`, then `coordinate` with their tasks, and return each outcome.

    Whatever happens, the held action is released and every call is ended
    before this returns, so a failing test never leaves a transaction open.
    """
    tasks = [asyncio.create_task(call) for call in calls]
    try:
        async with asyncio.timeout(TEST_DEADLINE_SECONDS):
            await coordinate(tasks)
            held.release.set()
            return await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        held.release.set()
        for task in tasks:
            _ = task.cancel()
        _ = await asyncio.gather(*tasks, return_exceptions=True)


def _request() -> IdempotencyRequest:
    return IdempotencyRequest(PilotId.new(), OPERATION, "key-1", "hash-1")


async def _start_first(
    held: HeldWrite, request: IdempotencyRequest
) -> asyncio.Task[WriteReceipt]:
    """Start a first call, and return once its action holds the key row."""
    first = asyncio.create_task(held(request))
    async with asyncio.timeout(WAITERS_DEADLINE_SECONDS):
        await held.entered.wait()
    return first


async def test_a_repeat_waits_for_the_first_call_and_returns_its_result(
    uow: Callable[[], SharedUnitOfWork], database: PostgresDatabase
) -> None:
    held = HeldWrite(uow)
    request = _request()
    first = await _start_first(held, request)

    async def coordinate(tasks: list[asyncio.Task[WriteReceipt]]) -> None:
        await _wait_for_lock_waiters(database, 1)
        # The repeat waits on the first call's key row, still uncommitted.
        assert not any(task.done() for task in tasks)

    [repeat] = await _together(held, [held(request)], coordinate)

    assert repeat == await first
    assert held.runs == 1
    assert await _count(database, JobRecord) == 1


async def test_a_repeat_runs_fresh_when_the_first_call_rolls_back(
    uow: Callable[[], SharedUnitOfWork], database: PostgresDatabase
) -> None:
    held = HeldWrite(uow, fail_first=True)
    request = _request()
    first = await _start_first(held, request)

    async def coordinate(_tasks: list[asyncio.Task[WriteReceipt]]) -> None:
        await _wait_for_lock_waiters(database, 1)

    [repeat] = await _together(held, [held(request)], coordinate)

    with pytest.raises(RuntimeError, match="the held write failed"):
        _ = await first
    assert not isinstance(repeat, BaseException), repeat
    assert held.runs == 2
    assert await _count(database, JobRecord) == 1
    assert await _count(database, IdempotencyKeyRecord) == 1


async def test_twenty_concurrent_calls_run_the_action_once(
    uow: Callable[[], SharedUnitOfWork], database: PostgresDatabase
) -> None:
    held = HeldWrite(uow)
    request = _request()
    barrier = asyncio.Barrier(CALLS)

    async def call() -> WriteReceipt:
        _ = await barrier.wait()
        return await held(request)

    async def coordinate(_tasks: list[asyncio.Task[WriteReceipt]]) -> None:
        await _wait_for_lock_waiters(database, CALLS - 1)

    outcomes = await _together(held, [call() for _ in range(CALLS)], coordinate)

    first = outcomes[0]
    assert all(outcome == first for outcome in outcomes), outcomes
    assert not isinstance(first, BaseException)
    assert held.runs == 1
    assert await _count(database, JobRecord) == 1
    assert await _count(database, IdempotencyKeyRecord) == 1
