"""The idempotency repositories and their maintenance: fake against Postgres."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import null, select
from sqlalchemy.ext.asyncio import create_async_engine

from tally_aloft.adapters.outbound.postgres.tables.idempotency_keys import (
    IdempotencyKeyRecord,
)
from tally_aloft.adapters.outbound.postgres.unit_of_work import unit_of_work_factory
from tally_aloft.application.shared.idempotency import IdempotencyRequest, StoredKey
from tally_aloft.domain.shared.ids import PilotId
from tests.fakes.clock import FakeClock
from tests.fakes.database import FakeDatabase, FakeIdempotencyRow
from tests.fakes.idempotency import key_id
from tests.fakes.unit_of_work import fake_unit_of_work_factory
from tests.support.idempotency_rows import REQUEST_HASH, insert_keys
from tests.support.postgres import connected

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.idempotency import (
        IdempotencyMaintenanceUnitOfWork,
    )
    from tests.support.postgres import PostgresDatabase

OPERATION = "test.contract"
RESULT: JsonValue = {"entry": {"hours": 1.5, "tags": ["night"]}, "note": None}
CUTOFF = datetime(2026, 6, 1, 12, tzinfo=UTC)


def _request(
    actor: PilotId | None = None,
    *,
    operation: str = OPERATION,
    key: str = "key-1",
    request_hash: str = "hash-1",
) -> IdempotencyRequest:
    return IdempotencyRequest(
        PilotId.new() if actor is None else actor, operation, key, request_hash
    )


@dataclass(frozen=True, slots=True)
class IdempotencyCase:
    """A unit-of-work factory under test, and how to inspect and seed its keys."""

    uow: Callable[[], IdempotencyMaintenanceUnitOfWork]
    # The committed row, read outside any unit of work under test.
    read: Callable[[IdempotencyRequest], Awaitable[StoredKey | None]]
    # Inserts this many keys of one new pilot, created at a given time.
    insert: Callable[[int, datetime], Awaitable[list[IdempotencyRequest]]]


@pytest.fixture
def fake_case() -> IdempotencyCase:
    clock = FakeClock()
    database = FakeDatabase(clock)

    async def read(request: IdempotencyRequest) -> StoredKey | None:
        row = database.idempotency_keys.get(key_id(request))
        if row is None:
            return None
        return StoredKey(row.request_hash, row.result, row.has_result)

    async def insert(count: int, created_at: datetime) -> list[IdempotencyRequest]:
        actor = PilotId.new()
        requests = [
            _request(actor, key=f"key-{n}", request_hash=REQUEST_HASH)
            for n in range(1, count + 1)
        ]
        for request in requests:
            database.idempotency_keys[key_id(request)] = FakeIdempotencyRow(
                request_hash=REQUEST_HASH,
                result=None,
                has_result=False,
                created_at=created_at,
            )
        return requests

    return IdempotencyCase(fake_unit_of_work_factory(database), read, insert)


@pytest.fixture
async def postgres_case(database: PostgresDatabase) -> AsyncGenerator[IdempotencyCase]:
    engine = create_async_engine(database.app_url)

    async def read(request: IdempotencyRequest) -> StoredKey | None:
        statement = select(
            IdempotencyKeyRecord.request_hash,
            IdempotencyKeyRecord.result,
            IdempotencyKeyRecord.result.is_not(null()),
        ).where(
            IdempotencyKeyRecord.pilot_id == request.actor.value,
            IdempotencyKeyRecord.operation == request.operation,
            IdempotencyKeyRecord.key == request.key,
        )
        async with connected(database.app_url) as connection:
            row = (await connection.execute(statement)).one_or_none()
        if row is None:
            return None
        return StoredKey(row[0], cast("JsonValue", row[1]), row[2])

    async def insert(count: int, created_at: datetime) -> list[IdempotencyRequest]:
        return await insert_keys(engine, count, created_at=created_at)

    yield IdempotencyCase(unit_of_work_factory(engine), read, insert)
    await engine.dispose()


# Parametrized by fixture name, as the jobs contract is.
@pytest.fixture(
    params=["fake_case", pytest.param("postgres_case", marks=pytest.mark.container)]
)
def case(request: pytest.FixtureRequest) -> IdempotencyCase:
    return cast("IdempotencyCase", request.getfixturevalue(cast("str", request.param)))


async def _reserve(case: IdempotencyCase, request: IdempotencyRequest) -> bool:
    async with case.uow() as uow:
        return await uow.idempotency.reserve(request)


async def _reserve_and_store(
    case: IdempotencyCase, request: IdempotencyRequest, result: JsonValue
) -> None:
    async with case.uow() as uow:
        assert await uow.idempotency.reserve(request)
        await uow.idempotency.store_result(request, result)


async def _find(case: IdempotencyCase, request: IdempotencyRequest) -> StoredKey | None:
    async with case.uow() as uow:
        return await uow.idempotency.find(request)


async def test_the_first_reserve_inserts_and_a_repeat_does_not(
    case: IdempotencyCase,
) -> None:
    request = _request()

    assert await _reserve(case, request)
    assert not await _reserve(case, request)
    assert await case.read(request) == StoredKey("hash-1", None, has_result=False)


async def test_a_stored_result_is_found_with_its_hash(case: IdempotencyCase) -> None:
    request = _request()
    await _reserve_and_store(case, request, RESULT)

    stored = await _find(case, request)

    assert stored == StoredKey("hash-1", RESULT, has_result=True)
    assert await case.read(request) == stored


async def test_a_json_null_result_is_a_stored_result(case: IdempotencyCase) -> None:
    request = _request()
    await _reserve_and_store(case, request, None)

    assert await _find(case, request) == StoredKey("hash-1", None, has_result=True)


async def test_a_repeat_finds_the_first_hash_not_its_own(
    case: IdempotencyCase,
) -> None:
    first = _request()
    await _reserve_and_store(case, first, RESULT)
    repeat = _request(first.actor, request_hash="hash-2")

    assert not await _reserve(case, repeat)
    assert await _find(case, repeat) == StoredKey("hash-1", RESULT, has_result=True)


async def test_keys_are_scoped_to_the_actor_and_the_operation(
    case: IdempotencyCase,
) -> None:
    first = _request()
    await _reserve_and_store(case, first, RESULT)
    other_actor = _request()
    other_operation = _request(first.actor, operation="test.contract_other")

    assert await _find(case, other_actor) is None
    assert await _find(case, other_operation) is None
    assert await _reserve(case, other_actor)
    assert await _reserve(case, other_operation)


async def test_a_unit_of_work_that_raises_keeps_no_key(case: IdempotencyCase) -> None:
    request = _request()
    msg = "the use case failed"

    async def failing_use_case() -> None:
        async with case.uow() as uow:
            assert await uow.idempotency.reserve(request)
            await uow.idempotency.store_result(request, RESULT)
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match=msg):
        await failing_use_case()

    assert await case.read(request) is None
    assert await _reserve(case, request)


async def test_a_missing_key_is_not_found(case: IdempotencyCase) -> None:
    assert await _find(case, _request()) is None


async def _delete(case: IdempotencyCase, limit: int) -> int:
    async with case.uow() as uow:
        return await uow.idempotency_maintenance.delete_created_before(CUTOFF, limit)


async def test_only_keys_created_before_the_cutoff_are_deleted(
    case: IdempotencyCase,
) -> None:
    [old] = await case.insert(1, CUTOFF - timedelta(hours=1))
    [recent] = await case.insert(1, CUTOFF + timedelta(hours=1))

    assert await _delete(case, 1000) == 1

    assert await case.read(old) is None
    assert await case.read(recent) is not None


async def test_a_delete_stops_at_its_limit(case: IdempotencyCase) -> None:
    old = await case.insert(3, CUTOFF - timedelta(hours=1))

    assert await _delete(case, 2) == 2

    remaining = [request for request in old if await case.read(request) is not None]
    assert len(remaining) == 1
    assert await _delete(case, 2) == 1
