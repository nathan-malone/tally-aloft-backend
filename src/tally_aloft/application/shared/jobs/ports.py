from typing import TYPE_CHECKING, Protocol

from tally_aloft.application.shared.unit_of_work import SharedUnitOfWork

if TYPE_CHECKING:
    from datetime import timedelta

    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.jobs.kinds import (
        ClaimedJob,
        JobId,
        JobKind,
        QueuedJob,
    )
    from tally_aloft.domain.shared.ids import PilotId


class JobRepository(Protocol):
    """Records a pilot's jobs. Every use case can reach it as `uow.jobs`."""

    async def record[P, R](
        self, actor: PilotId, kind: JobKind[P, R], payload: P
    ) -> QueuedJob:
        """Insert a `queued` job with the kind's `max_attempts` and `payload`."""
        ...


class JobRunner(Protocol):
    """
    Runs any pilot's jobs: the unscoped operations of the job lifecycle.

    Only the lifecycle and maintenance use cases reach it, through
    `JobRunnerUnitOfWork`. Every write after the claim is fenced on the
    `attempts` the claim set: it changes the row only while the row is
    `running` with that value, and returns whether it did.
    """

    async def claim(
        self, job_id: JobId, kind_name: str, lease: timedelta
    ) -> ClaimedJob[JsonValue] | None:
        """
        Take the job, or return None if it isn't free to take.

        It is free when it is this kind's, below `max_attempts`, and either
        `queued` or `running` with an expired lease. Taking it sets `running`,
        adds an attempt and leases it for `lease` from the database's `now()`.
        """
        ...

    async def renew(self, job_id: JobId, attempts: int, lease: timedelta) -> bool:
        """Extend the lease to `lease` from the database's `now()`."""
        ...

    async def complete(self, job_id: JobId, attempts: int, result: JsonValue) -> bool:
        """Set `succeeded`, store `result` and clear any earlier `error_code`."""
        ...

    async def fail(self, job_id: JobId, attempts: int, error_code: str) -> bool:
        """Record `error_code`; set `failed` at `max_attempts`, else `queued`."""
        ...


class JobRunnerUnitOfWork(SharedUnitOfWork, Protocol):
    """
    A unit of work that also exposes the unscoped `job_runner`.

    Domain units of work extend `SharedUnitOfWork`, not this, so feature use
    cases can't reach another pilot's jobs by accident.
    """

    @property
    def job_runner(self) -> JobRunner:
        """Return the job runner on this unit of work's session."""
        ...
