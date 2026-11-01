"""
Idempotency key rows written straight to Postgres, at times no call makes.

The contract suite and the cleanup's tests use these for keys of a chosen
age, and `seed_fake_keys` makes the same rows in a `FakeDatabase`. They run as
the application role, which may insert keys but update only their `result`,
so a key's `created_at` is set as it is inserted.
"""

from typing import TYPE_CHECKING, cast

from sqlalchemy import text

from tally_aloft.application.shared.idempotency import IdempotencyRequest
from tally_aloft.domain.shared.ids import PilotId
from tests.fakes.database import FakeIdempotencyRow
from tests.fakes.idempotency import key_id

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncEngine

    from tests.fakes.database import FakeDatabase

ROWS_OPERATION = "test.rows"
ROWS_REQUEST_HASH = "test-rows-hash"

_INSERT = text(
    """
    INSERT INTO idempotency_keys (pilot_id, operation, key, request_hash, created_at)
    SELECT :pilot_id, :operation, 'key-' || n, :request_hash, :created_at
    FROM generate_series(1, :count) AS n
    RETURNING key
    """
)


async def insert_keys(
    engine: AsyncEngine, count: int, *, created_at: datetime
) -> list[IdempotencyRequest]:
    """Insert `count` keys of one new pilot, created at `created_at`."""
    actor = PilotId.new()
    async with engine.begin() as connection:
        result = await connection.execute(
            _INSERT,
            {
                "pilot_id": actor.value,
                "operation": ROWS_OPERATION,
                "request_hash": ROWS_REQUEST_HASH,
                "created_at": created_at,
                "count": count,
            },
        )
        keys = cast("list[str]", result.scalars().all())
    return [
        IdempotencyRequest(actor, ROWS_OPERATION, key, ROWS_REQUEST_HASH)
        for key in keys
    ]


def seed_fake_keys(
    database: FakeDatabase, count: int, *, created_at: datetime
) -> list[IdempotencyRequest]:
    """Commit the keys `insert_keys` would, with no result, to a `FakeDatabase`."""
    actor = PilotId.new()
    requests = [
        IdempotencyRequest(actor, ROWS_OPERATION, f"key-{n}", ROWS_REQUEST_HASH)
        for n in range(1, count + 1)
    ]
    for request in requests:
        database.idempotency_keys[key_id(request)] = FakeIdempotencyRow(
            request_hash=ROWS_REQUEST_HASH,
            result=None,
            has_result=False,
            created_at=created_at,
        )
    return requests
