from datetime import timedelta
from typing import TYPE_CHECKING

from tally_aloft.adapters.inbound.jobs.tasks import scheduled_task

if TYPE_CHECKING:
    from tally_aloft.application.registry import UseCases

# The sweep runs every minute. Its timeout stays below that, so one
# scheduler's sweeps don't overlap; `SKIP LOCKED` makes an overlap safe anyway.
SWEEP_INTERVAL = timedelta(minutes=1)
SWEEP_TIMEOUT = timedelta(seconds=50)


async def sweep(use_cases: UseCases) -> None:
    """Recover stalled jobs, and fail the ones that can never run again."""
    await use_cases.sweep_jobs()


SWEEP_TASK = scheduled_task(
    "jobs.sweep", sweep, timeout=SWEEP_TIMEOUT, every=SWEEP_INTERVAL
)
