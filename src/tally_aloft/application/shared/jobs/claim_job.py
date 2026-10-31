from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.jobs.kinds import ClaimedJob, JobId, JobKind
    from tally_aloft.application.shared.jobs.ports import JobRunnerUnitOfWork


@dataclass(frozen=True, slots=True)
class ClaimJob:
    """Take a job for this task, leased for its kind's lease, if it is free."""

    uow: Callable[[], JobRunnerUnitOfWork]

    async def __call__[P, R](
        self, job_id: JobId, kind: JobKind[P, R]
    ) -> ClaimedJob[JsonValue] | None:
        """Return the claimed job with its stored payload, or None."""
        async with self.uow() as uow:
            return await uow.job_runner.claim(job_id, kind.name, kind.lease)
