from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime


class Clock(Protocol):
    """
    The current time, the only way application code reads it.

    Job leases are the one exception: they compare with the database's
    `now()` in SQL, so every worker uses one clock.
    """

    def now(self) -> datetime:
        """Return the current time, timezone-aware, in UTC."""
        ...
