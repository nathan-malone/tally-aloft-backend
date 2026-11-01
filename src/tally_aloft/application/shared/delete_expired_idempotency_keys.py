import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from tally_aloft.application.ports.clock import Clock
    from tally_aloft.application.shared.idempotency import (
        IdempotencyMaintenanceUnitOfWork,
    )

_logger = logging.getLogger(__name__)

# How long a key is kept, as the client contract promises.
KEY_RETENTION = timedelta(hours=24)
# The most keys one unit of work deletes.
DELETE_BATCH_SIZE = 1000


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteExpiredIdempotencyKeys:
    """
    Delete every idempotency key older than `retention`, in batches.

    The scheduler runs it every hour, through the cleanup task. Each batch is
    its own unit of work, so a run that Taskiq's timeout cancels keeps the
    batches it committed, and the next run deletes the rest. The cutoff is
    read once, so keys created during the run never extend it.
    """

    uow: Callable[[], IdempotencyMaintenanceUnitOfWork]
    clock: Clock
    retention: timedelta = KEY_RETENTION
    batch_size: int = DELETE_BATCH_SIZE

    async def __call__(self) -> int:
        """Delete the expired keys, and return how many were deleted."""
        cutoff = self.clock.now() - self.retention
        total = 0
        while True:
            async with self.uow() as uow:
                deleted = await uow.idempotency_maintenance.delete_created_before(
                    cutoff, self.batch_size
                )
            total += deleted
            if deleted < self.batch_size:
                break
        _logger.info("Deleted %d expired idempotency keys", total)
        return total
