from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import timedelta

    from tally_aloft.application.shared.jobs.kinds import JobId
    from tally_aloft.application.shared.jobs.ports import JobRunnerUnitOfWork


@dataclass(frozen=True, slots=True)
class RenewJobLease:
    """Extend a claimed job's lease, if this claim still holds it."""

    uow: Callable[[], JobRunnerUnitOfWork]

    async def __call__(self, job_id: JobId, attempts: int, lease: timedelta) -> bool:
        """Return whether the claim that set `attempts` still held the job."""
        async with self.uow() as uow:
            return await uow.job_runner.renew(job_id, attempts, lease)
