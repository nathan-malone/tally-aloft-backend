import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tally_aloft.application.shared.jobs.dispatch import dispatch_safely

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import timedelta

    from tally_aloft.application.ports.job_queue import JobQueue
    from tally_aloft.application.shared.jobs.ports import JobRunnerUnitOfWork

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class SweepJobs:
    """
    Recover stalled jobs, and fail the ones that can never run again.

    The scheduler runs it every minute, through the sweeper task. Each step
    handles at most `batch_size` rows, and skips rows another sweep has
    locked, so overlapping sweeps never take the same job.

    Taskiq's timeout can cancel a sweep at any point, and nothing unsafe is
    left. A step cancelled before its commit rolls back, and the next sweep
    redoes it. Jobs taken but not yet dispatched keep their new
    `last_dispatched_at`, so the first sweep after `stale_after` takes them.
    """

    uow: Callable[[], JobRunnerUnitOfWork]
    queue: JobQueue
    # The job kinds the workers register. A job of any other kind fails once
    # it is stale.
    kind_names: frozenset[str]
    stale_after: timedelta
    batch_size: int

    async def __call__(self) -> None:
        """Run the sweep's steps: fail, select, then dispatch."""
        # Fail: jobs that have used every attempt, or stale ones of unknown kind.
        async with self.uow() as uow:
            failed = await uow.job_runner.fail_unrunnable(
                self.kind_names, self.stale_after, self.batch_size
            )
        for job in failed:
            _logger.warning(
                "Job %s (%s) failed: %s", job.id.value, job.kind_name, job.error_code
            )
        # Select: stale jobs, marked dispatched now, committed before dispatch.
        async with self.uow() as uow:
            taken = await uow.job_runner.take_stale(self.stale_after, self.batch_size)
        # Dispatch: each taken job, logging any that fails, never raising.
        await dispatch_safely(self.queue, taken)
