import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tally_aloft.application.ports.health import DependencyCheck

_logger = logging.getLogger(__name__)

# The event loop holds only weak references to tasks, so a cancelled check
# that is still cleaning up stays here until it finishes.
_abandoned: set[asyncio.Task[None]] = set()


@dataclass(frozen=True, slots=True)
class CheckReadiness:
    """
    Run every dependency check concurrently and report the ones that fail.

    Each check has `timeout` seconds, so a hanging dependency fails rather than
    holding the answer up. A check still running then is cancelled and left to
    finish in the background: a check may delay its cancellation, as
    SQLAlchemy's connection cleanup does, and waiting for it would break the
    deadline.
    """

    checks: tuple[DependencyCheck, ...]
    timeout: float = 2.0

    async def __call__(self) -> tuple[str, ...]:
        """Return the names of the failed checks, in registration order."""
        if len(self.checks) == 0:
            return ()
        tasks = [asyncio.create_task(check.check()) for check in self.checks]
        try:
            _, pending = await asyncio.wait(tasks, timeout=self.timeout)
        finally:
            # Also on cancelling this call, so no check outlives it unasked.
            # Every task gets `_forget`, since a cancelled call skips the loop
            # below that reads the errors.
            for task in tasks:
                task.add_done_callback(_forget)
                if not task.done():
                    _ = task.cancel()
                    _abandoned.add(task)
        failed: list[str] = []
        for check, task in zip(self.checks, tasks, strict=True):
            error = "TimeoutError" if task in pending else _error(task)
            if error is not None:
                # The type only: an error's text can hold hosts or credentials.
                _logger.warning("Readiness check %s failed: %s", check.name, error)
                failed.append(check.name)
        return tuple(failed)


def _error(task: asyncio.Task[None]) -> str | None:
    """Return the type of a finished check's error, or None if it passed."""
    # A check's own `CancelledError` is a failure like any other.
    if task.cancelled():
        return asyncio.CancelledError.__qualname__
    error = task.exception()
    return None if error is None else type(error).__qualname__


def _forget(task: asyncio.Task[None]) -> None:
    """Drop a check's task once it finishes, and discard its outcome."""
    _abandoned.discard(task)
    # Retrieving the error stops asyncio logging it, text and all, as never
    # retrieved. Readiness reports it, or was cancelled.
    if not task.cancelled():
        _ = task.exception()
