import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tally_aloft.application.ports.job_queue import JobQueue
    from tally_aloft.application.shared.jobs.kinds import QueuedJob

_logger = logging.getLogger(__name__)

# How long one dispatch may take before it is abandoned.
DISPATCH_TIMEOUT = timedelta(seconds=5)


async def dispatch_safely(
    queue: JobQueue, jobs: Iterable[QueuedJob], *, limit: timedelta = DISPATCH_TIMEOUT
) -> None:
    """
    Dispatch each job, after the commit that recorded it, and never raise.

    A dispatch that fails or takes longer than `limit` is logged and left:
    the job is already `queued` in its row, and the sweeper re-dispatches it.
    Only the error's type is logged, because its text can hold hosts.
    """
    for job in jobs:
        try:
            async with asyncio.timeout(limit.total_seconds()):
                await queue.dispatch(job.id, job.kind_name)
        except Exception as error:  # noqa: BLE001  # Any failure leaves the job to the sweeper.
            _logger.warning(
                "Dispatching job %s (%s) failed: %s",
                job.id.value,
                job.kind_name,
                type(error).__qualname__,
            )
