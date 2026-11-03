from datetime import timedelta
from typing import TYPE_CHECKING

from tally_aloft.adapters.inbound.jobs.tasks import scheduled_task

if TYPE_CHECKING:
    from tally_aloft.application.registry import UseCases

# Keys live 24 hours, so an hourly cleanup keeps each for at most 25.
CLEANUP_INTERVAL = timedelta(hours=1)
# Room for many batches of deletes; a run cut short keeps what it committed.
CLEANUP_TIMEOUT = timedelta(seconds=300)


async def delete_expired_idempotency_keys(use_cases: UseCases) -> None:
    """Delete the idempotency keys older than 24 hours."""
    _ = await use_cases.delete_expired_idempotency_keys()


CLEANUP_TASK = scheduled_task(
    "idempotency.cleanup",
    delete_expired_idempotency_keys,
    timeout=CLEANUP_TIMEOUT,
    every=CLEANUP_INTERVAL,
)
