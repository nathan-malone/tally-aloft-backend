import asyncio
import logging
import time

import pytest

from tally_aloft.application.shared.check_readiness import CheckReadiness
from tests.fakes.health import FakeDependencyCheck

TIMEOUT = 0.05


async def test_no_checks_means_nothing_failed() -> None:
    assert await CheckReadiness(())() == ()


async def test_reports_only_the_failing_checks_in_order() -> None:
    checks = (
        FakeDependencyCheck("first", "fail"),
        FakeDependencyCheck("passing", "pass"),
        FakeDependencyCheck("second", "fail"),
    )

    assert await CheckReadiness(checks, TIMEOUT)() == ("first", "second")


async def test_a_hanging_check_fails_after_the_timeout() -> None:
    readiness = CheckReadiness((FakeDependencyCheck("stuck", "hang"),), TIMEOUT)
    started = time.monotonic()

    failed = await readiness()

    assert failed == ("stuck",)
    assert time.monotonic() - started < TIMEOUT + 1


async def test_logs_the_failure_type_without_its_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)

    await CheckReadiness((FakeDependencyCheck("failing", "fail"),), TIMEOUT)()

    assert "failing" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "boom" not in caplog.text


async def test_cancelling_the_readiness_call_propagates() -> None:
    task = asyncio.create_task(
        CheckReadiness((FakeDependencyCheck("stuck", "hang"),), timeout=60)()
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_check_raising_cancelled_error_fails_like_any_other() -> None:
    class CancellingCheck:
        name = "cancelling"

        async def check(self) -> None:
            raise asyncio.CancelledError

    assert await CheckReadiness((CancellingCheck(),), TIMEOUT)() == ("cancelling",)
