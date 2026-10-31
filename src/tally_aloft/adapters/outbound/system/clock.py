from datetime import UTC, datetime


class SystemClock:
    """The `Clock` port on the operating system's clock."""

    def now(self) -> datetime:
        """Return the current time, timezone-aware, in UTC."""
        return datetime.now(UTC)  # noqa: TID251  # The one place that reads the system clock.
