import logging
from datetime import UTC, datetime, timedelta

import pytest

from tally_aloft.application.shared.delete_expired_idempotency_keys import (
    DELETE_BATCH_SIZE,
    DeleteExpiredIdempotencyKeys,
)
from tests.fakes.clock import FakeClock
from tests.fakes.database import FakeDatabase, FakeKeyId
from tests.fakes.idempotency import key_id
from tests.fakes.unit_of_work import FakeUnitOfWork, fake_unit_of_work_factory
from tests.support.counting import CountingFactory
from tests.support.idempotency_rows import seed_fake_keys

NOW = datetime(2026, 6, 1, 12, tzinfo=UTC)


@pytest.fixture
def database() -> FakeDatabase:
    return FakeDatabase(FakeClock(NOW))


def _seed(database: FakeDatabase, count: int, age: timedelta) -> list[FakeKeyId]:
    """Commit `count` keys of one new pilot, created `age` before `NOW`."""
    requests = seed_fake_keys(database, count, created_at=NOW - age)
    return [key_id(request) for request in requests]


def _cleanup(
    database: FakeDatabase, batch_size: int = DELETE_BATCH_SIZE
) -> tuple[DeleteExpiredIdempotencyKeys, CountingFactory[FakeUnitOfWork]]:
    factory = CountingFactory(fake_unit_of_work_factory(database))
    use_case = DeleteExpiredIdempotencyKeys(
        uow=factory, clock=database.clock, batch_size=batch_size
    )
    return use_case, factory


async def test_only_keys_older_than_24_hours_are_deleted(
    database: FakeDatabase, caplog: pytest.LogCaptureFixture
) -> None:
    [old] = _seed(database, 1, timedelta(hours=25))
    [recent] = _seed(database, 1, timedelta(hours=1))
    cleanup, _ = _cleanup(database)

    with caplog.at_level(logging.INFO):
        deleted = await cleanup()

    assert deleted == 1
    assert set(database.idempotency_keys) == {recent}
    assert old not in database.idempotency_keys
    assert "Deleted 1 expired idempotency keys" in caplog.text


async def test_many_expired_keys_are_deleted_in_batches_of_1000(
    database: FakeDatabase,
) -> None:
    _ = _seed(database, 2500, timedelta(hours=25))
    cleanup, factory = _cleanup(database)

    assert await cleanup() == 2500

    assert database.idempotency_keys == {}
    assert factory.made == 3


@pytest.mark.parametrize(("expired", "units_of_work"), [(3, 2), (7, 3), (0, 1)])
async def test_a_short_batch_ends_the_run(
    database: FakeDatabase, expired: int, units_of_work: int
) -> None:
    _ = _seed(database, expired, timedelta(hours=25))
    cleanup, factory = _cleanup(database, batch_size=3)

    assert await cleanup() == expired

    assert database.idempotency_keys == {}
    assert factory.made == units_of_work


def test_a_batch_size_below_1_is_rejected(database: FakeDatabase) -> None:
    with pytest.raises(ValueError, match="batch size of at least 1"):
        _ = _cleanup(database, batch_size=0)
