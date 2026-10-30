from datetime import UTC, datetime, timedelta

from tally_aloft.adapters.outbound.system.clock import SystemClock


def test_now_is_the_current_time_in_utc() -> None:
    now = SystemClock().now()

    assert now.tzinfo is UTC
    assert abs(now - datetime.now(UTC)) < timedelta(seconds=1)
