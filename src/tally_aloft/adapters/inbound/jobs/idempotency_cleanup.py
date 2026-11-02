from datetime import timedelta

from tally_aloft.adapters.inbound.jobs.dependencies import UseCasesDep  # noqa: TC001  # Taskiq reads task annotations at run time.
from tally_aloft.adapters.inbound.jobs.tasks import TaskDefinition

# Keys live 24 hours, so an hourly cleanup keeps each for at most 25.
CLEANUP_INTERVAL = timedelta(hours=1)
# Room for many batches of deletes; a run cut short keeps what it committed.
CLEANUP_TIMEOUT = timedelta(seconds=300)


async def delete_expired_idempotency_keys(use_cases: UseCasesDep) -> None:
    """Delete the idempotency keys older than 24 hours."""
    _ = await use_cases.delete_expired_idempotency_keys()


CLEANUP_TASK = TaskDefinition(
    name="idempotency.cleanup",
    function=delete_expired_idempotency_keys,
    timeout=CLEANUP_TIMEOUT,
    every=CLEANUP_INTERVAL,
)
