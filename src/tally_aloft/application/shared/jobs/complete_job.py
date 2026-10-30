from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.jobs.kinds import JobId
    from tally_aloft.application.shared.jobs.ports import JobRunnerUnitOfWork


@dataclass(frozen=True, slots=True)
class CompleteJob:
    """Mark a claimed job `succeeded` with its result, if the claim still holds."""

    uow: Callable[[], JobRunnerUnitOfWork]

    async def __call__(self, job_id: JobId, attempts: int, result: JsonValue) -> bool:
        """Return whether the claim that set `attempts` still held the job."""
        async with self.uow() as uow:
            return await uow.job_runner.complete(job_id, attempts, result)
