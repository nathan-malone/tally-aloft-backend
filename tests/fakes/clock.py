from datetime import UTC, datetime, timedelta

_START = datetime(2026, 1, 1, tzinfo=UTC)


class FakeClock:
    """A clock that moves only when a test advances it."""

    def __init__(self, start: datetime = _START) -> None:
        self._now = start

    def now(self) -> datetime:
        """Return the current time."""
        return self._now

    def advance(self, delta: timedelta) -> None:
        """Move the time forward by `delta`."""
        self._now += delta
