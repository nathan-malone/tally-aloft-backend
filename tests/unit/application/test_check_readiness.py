import asyncio
import gc
import logging
import time
from typing import TYPE_CHECKING

import pytest

from tally_aloft.application.shared.check_readiness import CheckReadiness
from tests.fakes.health import FakeDependencyCheck

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

TIMEOUT = 0.05


@pytest.fixture
async def asyncio_reports() -> AsyncIterator[list[dict[str, object]]]:
    """Collect what the loop reports, such as a task error nobody retrieved."""
    loop = asyncio.get_running_loop()
    reports: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _, context: reports.append(context))
    yield reports
    loop.set_exception_handler(None)


async def _settle() -> None:
    """Let done callbacks run and finished tasks be collected."""
    await asyncio.sleep(0)
    _ = gc.collect()


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


async def test_cancelling_the_readiness_call_propagates(
    asyncio_reports: list[dict[str, object]],
) -> None:
    checks = (
        FakeDependencyCheck("failing", "fail"),
        FakeDependencyCheck("stuck", "hang"),
    )
    task = asyncio.create_task(CheckReadiness(checks, timeout=60)())
    await asyncio.sleep(TIMEOUT)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    # The failed check's error, text and all, isn't logged as never retrieved.
    await _settle()
    assert asyncio_reports == []


async def test_a_check_raising_cancelled_error_fails_like_any_other() -> None:
    class CancellingCheck:
        name = "cancelling"

        async def check(self) -> None:
            raise asyncio.CancelledError

    assert await CheckReadiness((CancellingCheck(),), TIMEOUT)() == ("cancelling",)


async def test_a_check_slow_to_cancel_fails_after_the_timeout(
    asyncio_reports: list[dict[str, object]],
) -> None:
    # Like SQLAlchemy's `async with engine.connect()`, it finishes its cleanup
    # before it lets a cancellation through, and the cleanup can fail.
    class SlowToCancelCheck:
        name = "slow_to_cancel"
        cancelled = False
        released = asyncio.Event()
        finished = asyncio.Event()

        async def check(self) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                await self.released.wait()
                self.finished.set()
                raise RuntimeError from None

    check = SlowToCancelCheck()
    asyncio.get_running_loop().call_later(2, check.released.set)
    started = time.monotonic()

    failed = await CheckReadiness((check,), TIMEOUT)()

    assert failed == ("slow_to_cancel",)
    assert time.monotonic() - started < TIMEOUT + 1
    check.released.set()
    await check.finished.wait()
    assert check.cancelled
    await _settle()
    assert asyncio_reports == []
