from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from tally_aloft.application.shared.jobs.kinds import JobId
    from tally_aloft.application.shared.jobs.ports import JobRunnerUnitOfWork


@dataclass(frozen=True, slots=True)
class FailJobAttempt:
    """
    Record a failed attempt, if the claim still holds the job.

    The job goes back to `queued` for the sweeper to retry, or to `failed`
    once it has used `max_attempts`.
    """

    uow: Callable[[], JobRunnerUnitOfWork]

    async def __call__(self, job_id: JobId, attempts: int, error_code: str) -> bool:
        """Return whether the claim that set `attempts` still held the job."""
        async with self.uow() as uow:
            return await uow.job_runner.fail(job_id, attempts, error_code)
