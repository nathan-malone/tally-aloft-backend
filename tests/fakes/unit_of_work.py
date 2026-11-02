from typing import TYPE_CHECKING, Self

from tests.fakes.database import working_copy
from tests.fakes.idempotency import (
    FakeIdempotencyMaintenance,
    FakeIdempotencyRepository,
    FakeIdempotencyTable,
)
from tests.fakes.jobs import FakeJobRepository, FakeJobRunner, FakeJobTable

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

    from tests.fakes.database import FakeDatabase


class FakeUnitOfWork:
    """
    One transaction on a `FakeDatabase`, for every unit-of-work Protocol.

    It copies the tables when entered. On a clean exit it writes back only the
    rows its repositories changed, so two that overlap keep each other's
    commits; on an exception it drops them. It models no row locks, so
    concurrency is tested on Postgres.
    """

    def __init__(self, database: FakeDatabase) -> None:
        self._database = database
        self._jobs: FakeJobTable | None = None
        self._idempotency_keys: FakeIdempotencyTable | None = None
        self._used = False

    async def __aenter__(self) -> Self:
        """Take a working copy of the tables."""
        if self._used:
            msg = "a unit of work is one transaction; make a new one for each use"
            raise RuntimeError(msg)
        self._used = True
        clock = self._database.clock
        self._jobs = working_copy(self._database.jobs, clock)
        self._idempotency_keys = working_copy(self._database.idempotency_keys, clock)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Write back the changed rows on a clean exit, or drop them."""
        jobs, self._jobs = self._jobs, None
        keys, self._idempotency_keys = self._idempotency_keys, None
        if exc_type is None and jobs is not None and keys is not None:
            jobs.write_back(self._database.jobs)
            keys.write_back(self._database.idempotency_keys)

    @property
    def jobs(self) -> FakeJobRepository:
        """Return the pilot-scoped job repository on the working copy."""
        return FakeJobRepository(_opened(self._jobs))

    @property
    def job_runner(self) -> FakeJobRunner:
        """Return the unscoped job runner on the working copy."""
        return FakeJobRunner(_opened(self._jobs))

    @property
    def idempotency(self) -> FakeIdempotencyRepository:
        """Return the pilot-scoped idempotency-key repository on the working copy."""
        return FakeIdempotencyRepository(_opened(self._idempotency_keys))

    @property
    def idempotency_maintenance(self) -> FakeIdempotencyMaintenance:
        """Return the unscoped idempotency maintenance on the working copy."""
        return FakeIdempotencyMaintenance(_opened(self._idempotency_keys))


def _opened[T](table: T | None) -> T:
    """Return `table`, which exists only inside `async with`."""
    if table is None:
        msg = "the unit of work has no tables outside `async with`"
        raise RuntimeError(msg)
    return table


def fake_unit_of_work_factory(database: FakeDatabase) -> Callable[[], FakeUnitOfWork]:
    """Return a factory making a new fake unit of work on `database` each call."""
    return lambda: FakeUnitOfWork(database)
