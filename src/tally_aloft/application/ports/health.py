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
        """Return when the dependency works, and raise when it doesn't."""
        ...
