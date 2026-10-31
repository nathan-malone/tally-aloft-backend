from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from tally_aloft.application.shared.jobs.kinds import JobId


class JobQueue(Protocol):
    """
    Sends a recorded job to the workers.

    The message carries only the job's ID, under its kind's name: the job row
    holds everything else. Delivery is at least once, and a job whose dispatch
    is lost is re-dispatched from its row by the sweeper.
    """

    async def dispatch(self, job_id: JobId, kind_name: str) -> None:
        """Send `job_id` to the task registered under `kind_name`."""
        ...
