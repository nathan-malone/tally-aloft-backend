import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tally_aloft.application.ports.health import DependencyCheck

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CheckReadiness:
    """
    Run every dependency check concurrently and report the ones that fail.

    Each check has `timeout` seconds, so a hanging dependency fails rather than
    holding the answer up.
    """

    checks: tuple[DependencyCheck, ...]
    timeout: float = 2.0

    async def __call__(self) -> tuple[str, ...]:
        """Return the names of the failed checks, in registration order."""
        results = await asyncio.gather(
            *(self._run(check) for check in self.checks), return_exceptions=True
        )
        failed: list[str] = []
        # Cancelling this call cancels `gather` itself, which raises. A
        # `CancelledError` here came from a check's own code, so it is a failure.
        for check, result in zip(self.checks, results, strict=True):
            if isinstance(result, BaseException):
                # The type only: an error's text can hold hosts or credentials.
                _logger.warning(
                    "Readiness check %s failed: %s",
                    check.name,
                    type(result).__qualname__,
                )
                failed.append(check.name)
        return tuple(failed)

    async def _run(self, check: DependencyCheck) -> None:
        async with asyncio.timeout(self.timeout):
            await check.check()
