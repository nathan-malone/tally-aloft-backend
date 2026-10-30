import logging
import time
from datetime import timedelta
from uuid import uuid7

import pytest

from tally_aloft.application.shared.jobs.dispatch import dispatch_safely
from tally_aloft.application.shared.jobs.kinds import JobId, JobStatus, QueuedJob
from tally_aloft.domain.shared.ids import PilotId
from tests.fakes.clock import FakeClock
from tests.fakes.database import FakeDatabase
from tests.fakes.job_queue import FakeJobQueue
from tests.fakes.unit_of_work import fake_unit_of_work_factory
from tests.support.jobs import ECHO, RecordingFailedError, RecordTestJob

# AC 4: a use case whose dispatch fails or hangs still returns within this.
RETURN_DEADLINE_SECONDS = 6


@pytest.mark.parametrize("outcome", ["fail", "hang"])
async def test_a_failed_dispatch_is_logged_and_the_use_case_returns(
    outcome: str, caplog: pytest.LogCaptureFixture
) -> None:
    database = FakeDatabase(FakeClock())
    queue = FakeJobQueue(outcome="fail" if outcome == "fail" else "hang")
    use_case = RecordTestJob(fake_unit_of_work_factory(database), queue)
    started = time.monotonic()

    with caplog.at_level(logging.WARNING):
        job = await use_case(PilotId.new(), {"n": 1})

    assert time.monotonic() - started < RETURN_DEADLINE_SECONDS
    assert database.jobs[job.id].status == JobStatus.QUEUED
    [record] = caplog.records
    assert str(job.id.value) in record.getMessage()
    assert ECHO.name in record.getMessage()
    assert queue.dispatched == []


async def test_a_use_case_that_raises_records_and_dispatches_nothing() -> None:
    database = FakeDatabase(FakeClock())
    queue = FakeJobQueue()
    use_case = RecordTestJob(fake_unit_of_work_factory(database), queue)

    with pytest.raises(RecordingFailedError):
        _ = await use_case(PilotId.new(), {"n": 1}, fail_after_record=True)

    assert database.jobs == {}
    assert queue.dispatched == []


async def test_a_use_case_dispatches_its_job_after_the_commit() -> None:
    database = FakeDatabase(FakeClock())
    queue = FakeJobQueue()
    use_case = RecordTestJob(fake_unit_of_work_factory(database), queue)

    job = await use_case(PilotId.new(), {"n": 1})

    assert queue.dispatched == [(job.id, ECHO.name)]


async def test_a_failed_dispatch_doesnt_stop_the_next() -> None:
    class FailsFirst:
        def __init__(self) -> None:
            self.calls = 0
            self.dispatched: list[tuple[JobId, str]] = []

        async def dispatch(self, job_id: JobId, kind_name: str) -> None:
            self.calls += 1
            if self.calls == 1:
                msg = "the queue refused the job"
                raise ConnectionError(msg)
            self.dispatched.append((job_id, kind_name))

    queue = FailsFirst()
    first = QueuedJob(JobId(uuid7()), "test.a")
    second = QueuedJob(JobId(uuid7()), "test.b")

    await dispatch_safely(queue, [first, second])

    assert queue.dispatched == [(second.id, "test.b")]


async def test_the_log_names_the_error_type_but_not_its_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    job = QueuedJob(JobId(uuid7()), "test.a")

    with caplog.at_level(logging.WARNING):
        await dispatch_safely(FakeJobQueue(outcome="fail"), [job])

    assert "ConnectionError" in caplog.text
    assert "refused" not in caplog.text


async def test_a_dispatch_is_abandoned_at_its_limit() -> None:
    job = QueuedJob(JobId(uuid7()), "test.a")
    started = time.monotonic()

    await dispatch_safely(
        FakeJobQueue(outcome="hang"), [job], limit=timedelta(milliseconds=50)
    )

    assert time.monotonic() - started < 1
