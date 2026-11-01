from datetime import timedelta

from tally_aloft.adapters.inbound.jobs.dependencies import UseCasesDep  # noqa: TC001  # Taskiq reads task annotations at run time.
from tally_aloft.adapters.inbound.jobs.tasks import TaskDefinition

# The sweep runs every minute. Its timeout stays below that, so one
# scheduler's sweeps don't overlap; `SKIP LOCKED` makes an overlap safe anyway.
SWEEP_INTERVAL = timedelta(minutes=1)
SWEEP_TIMEOUT = timedelta(seconds=50)


async def sweep(use_cases: UseCasesDep) -> None:
    """Recover stalled jobs, and fail the ones that can never run again."""
    await use_cases.sweep_jobs()


SWEEP_TASK = TaskDefinition(
    name="jobs.sweep", function=sweep, timeout=SWEEP_TIMEOUT, every=SWEEP_INTERVAL
)
