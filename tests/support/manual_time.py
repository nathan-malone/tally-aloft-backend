from datetime import UTC, datetime, timedelta

_START = datetime(2026, 1, 1, tzinfo=UTC)


class ManualTime:
    """
    A time source that moves only when a test advances it.

    It stands in for the `Clock` port until STORY-05 adds it and its fake.
    """

    def __init__(self, start: datetime = _START) -> None:
        self._now = start

    def now(self) -> datetime:
        """Return the current time."""
        return self._now

    def advance(self, delta: timedelta) -> None:
        """Move the time forward by `delta`."""
        self._now += delta
