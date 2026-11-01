from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from tally_aloft.application.shared.idempotency import StoredKey
from tests.fakes.database import FakeIdempotencyRow, FakeKeyId, FakeTable

if TYPE_CHECKING:
    from datetime import datetime

    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.idempotency import IdempotencyRequest

# A unit of work's working copy of the idempotency keys.
type FakeIdempotencyTable = FakeTable[FakeKeyId, FakeIdempotencyRow]


def key_id(request: IdempotencyRequest) -> FakeKeyId:
    """Return the fake table's key for the request's actor, operation and key."""
    return (request.actor, request.operation, request.key)


@dataclass(frozen=True, slots=True)
class FakeIdempotencyRepository:
    """
    The pilot-scoped `IdempotencyRepository` on a fake unit of work.

    It never waits: a key another unit of work reserved but hasn't committed
    isn't seen, so concurrent repeats are tested on Postgres.
    """

    table: FakeIdempotencyTable

    async def reserve(self, request: IdempotencyRequest) -> bool:
        """Insert the key row unless it exists, stamped with the clock's time."""
        key = key_id(request)
        if key in self.table.rows:
            return False
        row = FakeIdempotencyRow(
            request_hash=request.request_hash,
            result=None,
            has_result=False,
            created_at=self.table.clock.now(),
        )
        self.table.put(key, row)
        return True

    async def find(self, request: IdempotencyRequest) -> StoredKey | None:
        """Return the key row for the request's actor, operation and key."""
        row = self.table.rows.get(key_id(request))
        if row is None:
            return None
        return StoredKey(
            request_hash=row.request_hash, result=row.result, has_result=row.has_result
        )

    async def store_result(
        self, request: IdempotencyRequest, result: JsonValue
    ) -> None:
        """Store `result` on the key row."""
        key = key_id(request)
        row = self.table.rows.get(key)
        if row is not None:
            self.table.put(key, replace(row, result=result, has_result=True))


@dataclass(frozen=True, slots=True)
class FakeIdempotencyMaintenance:
    """The unscoped `IdempotencyMaintenance` on a fake unit of work."""

    table: FakeIdempotencyTable

    async def delete_created_before(self, cutoff: datetime, limit: int) -> int:
        """Delete up to `limit` keys created before `cutoff`."""
        old = [key for key, row in self.table.rows.items() if row.created_at < cutoff]
        for key in old[:limit]:
            self.table.remove(key)
        return len(old[:limit])
