from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, override

import pytest

from tally_aloft.application.shared.errors import IdempotencyKeyReusedError
from tally_aloft.application.shared.idempotency import IdempotencyRequest, run_once
from tally_aloft.domain.shared.ids import PilotId
from tests.fakes.clock import FakeClock
from tests.fakes.database import FakeDatabase, FakeIdempotencyRow
from tests.fakes.idempotency import FakeIdempotencyRepository, key_id
from tests.fakes.unit_of_work import FakeUnitOfWork, fake_unit_of_work_factory
from tests.support.codecs import JSON
from tests.support.idempotency import (
    OPERATION,
    RECEIPT_CODEC,
    IdempotentWrite,
    WriteCommand,
)

if TYPE_CHECKING:
    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.idempotency import StoredKey


@pytest.fixture
def database() -> FakeDatabase:
    return FakeDatabase(FakeClock())


@pytest.fixture
def write(database: FakeDatabase) -> IdempotentWrite:
    return IdempotentWrite(fake_unit_of_work_factory(database))


def _command(
    actor: PilotId | None = None, *, request_hash: str = "hash-1", fail: bool = False
) -> WriteCommand:
    return WriteCommand(
        actor=PilotId.new() if actor is None else actor,
        key="key-1",
        request_hash=request_hash,
        note="one hour",
        fail=fail,
    )


def _request(command: WriteCommand) -> IdempotencyRequest:
    return IdempotencyRequest(
        command.actor, OPERATION, command.key, command.request_hash
    )


async def test_a_first_call_runs_once_and_stores_its_encoded_result(
    write: IdempotentWrite, database: FakeDatabase
) -> None:
    command = _command()

    receipt = await write(command)

    assert len(database.jobs) == 1
    row = database.idempotency_keys[key_id(_request(command))]
    assert row.result == RECEIPT_CODEC.encode(receipt)
    assert receipt == RECEIPT_CODEC.decode(row.result)


class RoundingCodec:
    """Stores a float to two places, so it doesn't round-trip exactly."""

    def encode(self, value: float) -> JsonValue:
        return round(value, 2)

    def decode(self, data: JsonValue) -> float:
        assert isinstance(data, float)
        return data


async def test_a_first_call_returns_its_result_as_a_repeat_would(
    database: FakeDatabase,
) -> None:
    request = IdempotencyRequest(PilotId.new(), OPERATION, "key-1", "hash-1")

    async def action() -> float:
        return 1.23456

    async with FakeUnitOfWork(database) as uow:
        first = await run_once(uow, request, action, RoundingCodec())
    async with FakeUnitOfWork(database) as uow:
        repeat = await run_once(uow, request, action, RoundingCodec())

    assert first == repeat == 1.23


async def test_a_repeat_returns_the_stored_result_without_running(
    write: IdempotentWrite, database: FakeDatabase
) -> None:
    command = _command()
    first = await write(command)

    repeat = await write(command)

    assert repeat == first
    assert len(database.jobs) == 1


async def test_a_repeat_with_another_hash_is_refused(
    write: IdempotentWrite, database: FakeDatabase
) -> None:
    command = _command()
    _ = await write(command)

    with pytest.raises(IdempotencyKeyReusedError) as error:
        _ = await write(_command(command.actor, request_hash="hash-2"))

    assert error.value.code == "idempotency_key_reused"
    assert len(database.jobs) == 1


async def test_an_action_that_raises_keeps_no_key_and_a_retry_runs(
    write: IdempotentWrite, database: FakeDatabase
) -> None:
    command = _command(fail=True)

    with pytest.raises(RuntimeError, match="the test write failed"):
        _ = await write(command)

    assert database.idempotency_keys == {}
    assert database.jobs == {}
    receipt = await write(_command(command.actor))
    assert list(database.jobs) == [receipt.job_id]


class FailingCodec:
    """A codec whose `encode` raises."""

    def encode(self, value: int) -> JsonValue:
        msg = f"can't encode {value}"
        raise ValueError(msg)

    def decode(self, data: JsonValue) -> int:
        assert isinstance(data, int)
        return data


async def test_an_encode_that_raises_keeps_no_key(database: FakeDatabase) -> None:
    request = IdempotencyRequest(PilotId.new(), OPERATION, "key-1", "hash-1")

    async def action() -> int:
        return 7

    with pytest.raises(ValueError, match="can't encode 7"):
        async with FakeUnitOfWork(database) as uow:
            _ = await run_once(uow, request, action, FailingCodec())

    assert database.idempotency_keys == {}


async def test_two_actors_with_one_key_each_run_once(
    write: IdempotentWrite, database: FakeDatabase
) -> None:
    first = await write(_command())
    second = await write(_command())

    assert first != second
    assert len(database.jobs) == 2


async def test_two_operations_with_one_key_each_run_once(
    database: FakeDatabase,
) -> None:
    actor = PilotId.new()
    runs: list[str] = []

    async def action(operation: str) -> JsonValue:
        runs.append(operation)
        return len(runs)

    for operation in ("test.first", "test.second"):
        request = IdempotencyRequest(actor, operation, "key-1", "hash-1")
        async with FakeUnitOfWork(database) as uow:
            _ = await run_once(uow, request, partial(action, operation), JSON)

    assert runs == ["test.first", "test.second"]


@dataclass(frozen=True, slots=True)
class VanishingKeys(FakeIdempotencyRepository):
    """Keys that conflict on insert, then are gone when read."""

    @override
    async def reserve(
        self, request: IdempotencyRequest
    ) -> bool:  # The Protocol's signature.
        return False

    @override
    async def find(
        self, request: IdempotencyRequest
    ) -> StoredKey | None:  # The Protocol's signature.
        return None


class VanishingUnitOfWork(FakeUnitOfWork):
    @property
    @override
    def idempotency(self) -> VanishingKeys:
        return VanishingKeys(super().idempotency.table)


async def _never() -> int:
    msg = "the action must not run"
    raise AssertionError(msg)


async def test_a_key_deleted_between_insert_and_read_raises(
    database: FakeDatabase,
) -> None:
    request = IdempotencyRequest(PilotId.new(), OPERATION, "key-1", "hash-1")

    with pytest.raises(RuntimeError, match="deleted while it was being read"):
        async with VanishingUnitOfWork(database) as uow:
            _ = await run_once(uow, request, _never, FailingCodec())


async def test_a_key_committed_without_a_result_raises(database: FakeDatabase) -> None:
    request = IdempotencyRequest(PilotId.new(), OPERATION, "key-1", "hash-1")
    database.idempotency_keys[key_id(request)] = FakeIdempotencyRow(
        request_hash="hash-1",
        result=None,
        has_result=False,
        created_at=database.clock.now(),
    )

    with pytest.raises(RuntimeError, match="committed without a result"):
        async with FakeUnitOfWork(database) as uow:
            _ = await run_once(uow, request, _never, FailingCodec())
