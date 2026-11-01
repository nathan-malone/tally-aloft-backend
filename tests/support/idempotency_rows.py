"""
Idempotency key rows written straight to Postgres, at times no call makes.

The cleanup's tests use these for keys of a chosen age. They run as the
application role, which may insert keys but update only their `result`, so
a key's `created_at` is set as it is inserted.
"""

from typing import TYPE_CHECKING, cast

from sqlalchemy import text

from tally_aloft.application.shared.idempotency import IdempotencyRequest
from tally_aloft.domain.shared.ids import PilotId

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncEngine

OPERATION = "test.rows"
REQUEST_HASH = "test-rows-hash"

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
                "operation": OPERATION,
                "request_hash": REQUEST_HASH,
                "created_at": created_at,
                "count": count,
            },
        )
        keys = cast("list[str]", result.scalars().all())
    return [IdempotencyRequest(actor, OPERATION, key, REQUEST_HASH) for key in keys]
