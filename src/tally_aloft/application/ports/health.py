from typing import Protocol


class DependencyCheck(Protocol):
    """
    One dependency that readiness checks, such as a database or a cache.

    Each outbound adapter for a data store supplies one.
    """

    @property
    def name(self) -> str:
        """Return the name readiness reports when this check fails."""
        ...

    async def check(self) -> None:
        """
        Return when the dependency works, and raise when it doesn't.

        Readiness cancels a check that runs past its deadline and doesn't wait
        for it to stop, so let cancellation through promptly: a check that
        never ends leaves a task behind on every readiness call.
        """
        ...
