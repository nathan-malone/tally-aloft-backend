"""The idempotency cleanup on real Postgres, called directly and as a task."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from tally_aloft.adapters.inbound.jobs.idempotency_cleanup import CLEANUP_TASK
from tally_aloft.adapters.outbound.postgres.tables.idempotency_keys import (
    IdempotencyKeyRecord,
)
from tally_aloft.adapters.outbound.postgres.unit_of_work import (
    PostgresUnitOfWork,
    unit_of_work_factory,
)
from tally_aloft.application.shared.delete_expired_idempotency_keys import (
    DeleteExpiredIdempotencyKeys,
)
from tally_aloft.bootstrap.settings import Settings
from tally_aloft.bootstrap.worker import create_worker
from tests.fakes.clock import FakeClock
from tests.support.counting import CountingFactory
from tests.support.idempotency_rows import insert_keys
from tests.support.in_process_worker import eventually, kick, running_worker
from tests.support.jobs import TEST_IDLE_TIMEOUT
from tests.support.tasks import TEST_TASKS

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncEngine

    from tests.support.postgres import PostgresDatabase

pytestmark = pytest.mark.container

NOW = datetime(2026, 6, 1, 12, tzinfo=UTC)


@pytest.fixture
async def engine(database: PostgresDatabase) -> AsyncGenerator[AsyncEngine]:
    engine = create_async_engine(database.app_url)
    yield engine
    await engine.dispose()


async def _key_count(engine: AsyncEngine) -> int:
    async with engine.connect() as connection:
        statement = select(func.count()).select_from(IdempotencyKeyRecord)
        return (await connection.execute(statement)).scalar_one()


def _cleanup(
    engine: AsyncEngine,
) -> tuple[DeleteExpiredIdempotencyKeys, CountingFactory[PostgresUnitOfWork]]:
    factory = CountingFactory(unit_of_work_factory(engine))
    return DeleteExpiredIdempotencyKeys(uow=factory, clock=FakeClock(NOW)), factory


async def test_only_keys_older_than_24_hours_are_deleted(engine: AsyncEngine) -> None:
    _ = await insert_keys(engine, 1, created_at=NOW - timedelta(hours=25))
    _ = await insert_keys(engine, 1, created_at=NOW - timedelta(hours=1))
    cleanup, _ = _cleanup(engine)

    assert await cleanup() == 1

    assert await _key_count(engine) == 1


async def test_many_expired_keys_are_deleted_in_batches_of_1000(
    engine: AsyncEngine,
) -> None:
    _ = await insert_keys(engine, 2500, created_at=NOW - timedelta(hours=25))
    cleanup, factory = _cleanup(engine)

    assert await cleanup() == 2500

    assert await _key_count(engine) == 0
    assert factory.made == 3


async def test_the_cleanup_task_deletes_expired_keys(
    database: PostgresDatabase, valkey_url: str, engine: AsyncEngine
) -> None:
    settings = Settings.for_tests(
        database_url=database.app_url,
        valkey_url=valkey_url,
        broker_idle_timeout=int(TEST_IDLE_TIMEOUT.total_seconds()),
    )
    # The worker's cleanup reads the system clock.
    now = datetime.now(UTC)
    _ = await insert_keys(engine, 1, created_at=now - timedelta(hours=25))
    _ = await insert_keys(engine, 1, created_at=now)
    broker = create_worker(settings, TEST_TASKS)

    async def one_left() -> bool:
        return await _key_count(engine) == 1

    async with running_worker(broker):
        await kick(broker, CLEANUP_TASK.name)
        await eventually(one_left)
