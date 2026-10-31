from dataclasses import dataclass
from typing import TYPE_CHECKING

from taskiq.kicker import AsyncKicker

if TYPE_CHECKING:
    from taskiq import AsyncBroker

    from tally_aloft.application.shared.jobs.kinds import JobId


@dataclass(frozen=True, slots=True)
class TaskiqJobQueue:
    """
    The `JobQueue` port on the Taskiq broker.

    It kicks tasks by name, so it never imports the task functions in
    `adapters/inbound/jobs`. The worker applies each task's registered labels.
    """

    broker: AsyncBroker

    async def dispatch(self, job_id: JobId, kind_name: str) -> None:
        """Kick the task named `kind_name` with the job's ID as its one argument."""
        _ = await AsyncKicker(kind_name, self.broker, {}).kiq(str(job_id.value))
