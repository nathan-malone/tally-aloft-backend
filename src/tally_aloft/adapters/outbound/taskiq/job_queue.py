from dataclasses import dataclass
from typing import TYPE_CHECKING

from opentelemetry.trace.propagation.tracecontext import (
    TraceContextTextMapPropagator,
)
from taskiq.kicker import AsyncKicker

if TYPE_CHECKING:
    from taskiq import AsyncBroker

    from tally_aloft.application.shared.jobs.kinds import JobId

# Only the trace context travels: clients' `baggage` stays out of messages.
# `adapters/inbound/jobs/tracing.py` reads it back from the labels.
_PROPAGATOR = TraceContextTextMapPropagator()


@dataclass(frozen=True, slots=True)
class TaskiqJobQueue:
    """
    The `JobQueue` port on the Taskiq broker.

    It kicks tasks by name, so it never imports the task functions in
    `adapters/inbound/jobs`. The worker applies each task's registered labels.
    Each message carries the current W3C trace context in its labels, so the
    job's task span continues the dispatcher's trace.
    """

    broker: AsyncBroker

    async def dispatch(self, job_id: JobId, kind_name: str) -> None:
        """Kick the task named `kind_name` with the job's ID as its one argument."""
        labels: dict[str, str] = {}
        _PROPAGATOR.inject(labels)
        _ = await AsyncKicker(kind_name, self.broker, labels).kiq(str(job_id.value))
