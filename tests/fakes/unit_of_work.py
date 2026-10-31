from typing import TYPE_CHECKING, Self

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
        self._used = False

    async def __aenter__(self) -> Self:
        """Take a working copy of the tables."""
        if self._used:
            msg = "a unit of work is one transaction; make a new one for each use"
            raise RuntimeError(msg)
        self._used = True
        self._jobs = FakeJobTable(
            rows=dict(self._database.jobs), changed=set(), clock=self._database.clock
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Write back the changed rows on a clean exit, or drop them."""
        jobs, self._jobs = self._jobs, None
        if exc_type is None and jobs is not None:
            for job_id in jobs.changed:
                self._database.jobs[job_id] = jobs.rows[job_id]

    @property
    def jobs(self) -> FakeJobRepository:
        """Return the pilot-scoped job repository on the working copy."""
        return FakeJobRepository(self._table())

    @property
    def job_runner(self) -> FakeJobRunner:
        """Return the unscoped job runner on the working copy."""
        return FakeJobRunner(self._table())

    def _table(self) -> FakeJobTable:
        if self._jobs is None:
            msg = "the unit of work has no tables outside `async with`"
            raise RuntimeError(msg)
        return self._jobs


def fake_unit_of_work_factory(database: FakeDatabase) -> Callable[[], FakeUnitOfWork]:
    """Return a factory making a new fake unit of work on `database` each call."""
    return lambda: FakeUnitOfWork(database)
