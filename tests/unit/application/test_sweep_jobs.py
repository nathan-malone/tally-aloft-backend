import logging
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from tally_aloft.application.shared.jobs.kinds import (
    ATTEMPTS_EXHAUSTED_CODE,
    UNKNOWN_KIND_CODE,
    JobId,
    JobKind,
    JobStatus,
    QueuedJob,
)
from tally_aloft.application.shared.jobs.sweep_jobs import SweepJobs
from tally_aloft.domain.shared.ids import PilotId
from tests.fakes.clock import FakeClock
from tests.fakes.database import FakeDatabase
from tests.fakes.job_queue import FakeJobQueue
from tests.fakes.unit_of_work import fake_unit_of_work_factory
from tests.support.jobs import ECHO, FAIL

if TYPE_CHECKING:
    from tally_aloft.application.shared.codec import JsonValue

STALE_AFTER = timedelta(minutes=5)
REMOVED = "test.removed"
PAST_THE_THRESHOLD = STALE_AFTER + timedelta(minutes=1)


@dataclass(slots=True)
class CommittedRowsQueue:
    """A queue that records each dispatched job's row as it was committed then."""

    database: FakeDatabase
    seen: list[tuple[str, object]] = field(default_factory=list)

    async def dispatch(self, job_id: JobId, kind_name: str) -> None:
        row = self.database.jobs[job_id]
        self.seen.append((kind_name, row.last_dispatched_at))


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def database(clock: FakeClock) -> FakeDatabase:
    return FakeDatabase(clock)


def _sweep(
    database: FakeDatabase,
    queue: FakeJobQueue | CommittedRowsQueue,
    *,
    batch_size: int = 500,
) -> SweepJobs:
    return SweepJobs(
        uow=fake_unit_of_work_factory(database),
        queue=queue,
        kind_names=frozenset({ECHO.name, FAIL.name}),
        stale_after=STALE_AFTER,
        batch_size=batch_size,
    )


async def _record(
    database: FakeDatabase, kind: JobKind[JsonValue, JsonValue] = ECHO
) -> QueuedJob:
    async with fake_unit_of_work_factory(database)() as uow:
        return await uow.jobs.record(PilotId.new(), kind, None)


async def _record_unknown(database: FakeDatabase) -> QueuedJob:
    """Record a job, then give it a kind no worker registers."""
    job = await _record(database)
    database.jobs[job.id] = replace(database.jobs[job.id], kind=REMOVED)
    return QueuedJob(job.id, REMOVED)


async def test_a_stale_job_is_dispatched(
    database: FakeDatabase, clock: FakeClock
) -> None:
    job = await _record(database)
    clock.advance(PAST_THE_THRESHOLD)
    queue = FakeJobQueue()

    await _sweep(database, queue)()

    assert queue.dispatched == [(job.id, ECHO.name)]
    assert database.jobs[job.id].last_dispatched_at == clock.now()


async def test_failing_runs_first_so_an_exhausted_stale_job_is_not_dispatched(
    database: FakeDatabase, clock: FakeClock
) -> None:
    job = await _record(database, FAIL)
    row = database.jobs[job.id]
    database.jobs[job.id] = replace(row, attempts=row.max_attempts)
    clock.advance(PAST_THE_THRESHOLD)
    queue = FakeJobQueue()

    await _sweep(database, queue)()

    assert queue.dispatched == []
    failed = database.jobs[job.id]
    assert (failed.status, failed.error_code) == (
        JobStatus.FAILED,
        ATTEMPTS_EXHAUSTED_CODE,
    )


async def test_jobs_are_dispatched_after_their_commit(
    database: FakeDatabase, clock: FakeClock
) -> None:
    _ = await _record(database)
    clock.advance(PAST_THE_THRESHOLD)
    queue = CommittedRowsQueue(database)

    await _sweep(database, queue)()

    assert queue.seen == [(ECHO.name, clock.now())]


async def test_a_job_of_an_unknown_kind_is_failed_and_logged(
    database: FakeDatabase, caplog: pytest.LogCaptureFixture
) -> None:
    job = await _record_unknown(database)
    queue = FakeJobQueue()

    with caplog.at_level(logging.WARNING):
        await _sweep(database, queue)()

    assert database.jobs[job.id].error_code == UNKNOWN_KIND_CODE
    assert queue.dispatched == []
    [record] = caplog.records
    message = record.getMessage()
    assert str(job.id.value) in message
    assert REMOVED in message
    assert UNKNOWN_KIND_CODE in message


async def test_each_step_stops_at_the_batch_size(
    database: FakeDatabase, clock: FakeClock
) -> None:
    stale = [await _record(database) for _ in range(2)]
    clock.advance(PAST_THE_THRESHOLD)
    # Recorded after the clock moved, so they aren't stale: only step 1 can
    # touch them, and step 2's only candidates are the two stale jobs.
    unknown = [await _record_unknown(database) for _ in range(2)]
    queue = FakeJobQueue()

    await _sweep(database, queue, batch_size=1)()

    statuses = sorted(database.jobs[job.id].status for job in unknown)
    assert statuses == [JobStatus.FAILED, JobStatus.QUEUED]
    assert len(queue.dispatched) == 1
    assert queue.dispatched[0][0] in {job.id for job in stale}
