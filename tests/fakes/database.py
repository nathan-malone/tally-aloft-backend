from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from tally_aloft.application.ports.clock import Clock
    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.jobs.kinds import JobId
    from tally_aloft.domain.shared.ids import PilotId


@dataclass(frozen=True, slots=True, kw_only=True)
class FakeJobRow:
    """One row of the fake `jobs` table, as Postgres stores it."""

    id: JobId
    pilot_id: PilotId
    kind: str
    status: str
    attempts: int
    max_attempts: int
    lease_until: datetime | None
    last_dispatched_at: datetime
    payload: JsonValue
    result: JsonValue
    error_code: str | None


# A fake idempotency key's identity: its pilot, operation and key.
type FakeKeyId = tuple[PilotId, str, str]


@dataclass(frozen=True, slots=True, kw_only=True)
class FakeIdempotencyRow:
    """
    One row of the fake `idempotency_keys` table, as Postgres stores it.

    `has_result` stands for `result IS NOT NULL`, which tells a stored JSON
    `null` from no result.
    """

    request_hash: str
    result: JsonValue
    has_result: bool
    created_at: datetime


@dataclass(slots=True)
class FakeDatabase:
    """
    Every fake table, which fake units of work read and commit to.

    `clock` stands in for the database's `now()`. A new fake table joins this
    class, and `FakeUnitOfWork` copies and writes it back as it does `jobs`.
    """

    clock: Clock
    jobs: dict[JobId, FakeJobRow] = field(default_factory=dict)
    idempotency_keys: dict[FakeKeyId, FakeIdempotencyRow] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FakeTable[K, R]:
    """A unit of work's working copy of one fake table, and the keys it changed."""

    rows: dict[K, R]
    changed: set[K]
    clock: Clock

    def put(self, key: K, row: R) -> None:
        """Store `row` under `key` in the working copy and mark it changed."""
        self.rows[key] = row
        self.changed.add(key)

    def remove(self, key: K) -> None:
        """Delete the row under `key` from the working copy and mark it changed."""
        del self.rows[key]
        self.changed.add(key)

    def write_back(self, target: dict[K, R]) -> None:
        """Commit the changed rows to `target`, deleting the removed ones."""
        for key in self.changed:
            if key in self.rows:
                target[key] = self.rows[key]
            else:
                _ = target.pop(key, None)


def working_copy[K, R](rows: dict[K, R], clock: Clock) -> FakeTable[K, R]:
    """Return a working copy of `rows`, with nothing changed yet."""
    return FakeTable(rows=dict(rows), changed=set(), clock=clock)
