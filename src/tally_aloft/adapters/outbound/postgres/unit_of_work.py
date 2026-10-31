from typing import TYPE_CHECKING, Self

from sqlalchemy.ext.asyncio import async_sessionmaker

from tally_aloft.adapters.outbound.postgres.jobs import (
    PostgresJobRepository,
    PostgresJobRunner,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager
    from types import TracebackType

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession


class PostgresUnitOfWork:
    """
    One session and one transaction, for every unit-of-work Protocol.

    Each instance is used for one `async with`: it commits on a clean exit and
    rolls back on any exception, cancellation included. Repositories are built
    on `session` when it's entered; use cases see only the Protocol.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Make a unit of work that opens its session from `sessions`."""
        self._sessions = sessions
        self._transaction: AbstractAsyncContextManager[AsyncSession] | None = None
        self._session: AsyncSession | None = None
        self._jobs: PostgresJobRepository | None = None
        self._job_runner: PostgresJobRunner | None = None
        self._used = False

    async def __aenter__(self) -> Self:
        """Open the session and begin its transaction."""
        if self._used:
            msg = "a unit of work is one transaction; make a new one for each use"
            raise RuntimeError(msg)
        self._used = True
        transaction = self._sessions.begin()
        self._session = await transaction.__aenter__()
        self._transaction = transaction
        self._jobs = PostgresJobRepository(self._session)
        self._job_runner = PostgresJobRunner(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Commit on a clean exit or roll back on an exception, then close."""
        transaction = self._transaction
        self._transaction = None
        self._session = None
        self._jobs = None
        self._job_runner = None
        if transaction is not None:
            _ = await transaction.__aexit__(exc_type, exc, traceback)

    @property
    def session(self) -> AsyncSession:
        """Return the open session, for the repositories built on it."""
        return _opened(self._session)

    @property
    def jobs(self) -> PostgresJobRepository:
        """Return the pilot-scoped job repository on the open session."""
        return _opened(self._jobs)

    @property
    def job_runner(self) -> PostgresJobRunner:
        """Return the unscoped job runner on the open session."""
        return _opened(self._job_runner)


def _opened[T](part: T | None) -> T:
    """Return `part`, which exists only inside `async with`."""
    if part is None:
        msg = "the unit of work has no session outside `async with`"
        raise RuntimeError(msg)
    return part


def unit_of_work_factory(engine: AsyncEngine) -> Callable[[], PostgresUnitOfWork]:
    """Return the factory use cases call, making a new unit of work each time."""
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return lambda: PostgresUnitOfWork(sessions)
