"""The task wrapper on the fakes, with sub-second leases and time limits."""

import asyncio
import logging
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Self, override
from uuid import uuid7

import anyio
import pytest

from tally_aloft.adapters.inbound.jobs.run_job import run_job
from tally_aloft.application.shared.jobs.complete_job import CompleteJob
from tally_aloft.application.shared.jobs.kinds import JobId, JobKind, JobStatus
from tally_aloft.domain.shared.ids import PilotId
from tests.fakes.clock import FakeClock
from tests.fakes.database import FakeDatabase
from tests.fakes.unit_of_work import FakeUnitOfWork, fake_unit_of_work_factory
from tests.support.codecs import JSON
from tests.support.jobs import BodyFailedError
from tests.support.use_cases import job_use_cases

if TYPE_CHECKING:
    from tally_aloft.application.registry import UseCases
    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.jobs.kinds import (
        ClaimedJob,
        JobBody,
        QueuedJob,
    )
    from tests.fakes.database import FakeJobRow

type Kind = JobKind[JsonValue, JsonValue]
type Body = JobBody[JsonValue, JsonValue]

KIND: Kind = JobKind(
    name="test.unit",
    payload_codec=JSON,
    result_codec=JSON,
    lease=timedelta(milliseconds=200),
    time_limit=timedelta(seconds=2),
)
WAIT_SECONDS = 5


class UndecodableCodec:
    """A codec whose stored data never decodes."""

    def encode(self, value: JsonValue) -> JsonValue:
        return value

    def decode(self, data: JsonValue) -> JsonValue:
        msg = f"can't decode {data!r}"
        raise ValueError(msg)


class BrokenUnitOfWork(FakeUnitOfWork):
    """A unit of work whose database never answers."""

    @override
    async def __aenter__(self) -> Self:
        msg = "the database is unreachable"
        raise ConnectionError(msg)


@pytest.fixture
def database() -> FakeDatabase:
    return FakeDatabase(FakeClock())


@pytest.fixture
def use_cases(database: FakeDatabase) -> UseCases:
    return job_use_cases(fake_unit_of_work_factory(database))


async def _recorded(database: FakeDatabase, kind: Kind = KIND) -> QueuedJob:
    async with FakeUnitOfWork(database) as uow:
        return await uow.jobs.record(PilotId.new(), kind, {"n": 1})


async def _run(
    use_cases: UseCases, job: QueuedJob, body: Body, kind: Kind = KIND
) -> None:
    await asyncio.wait_for(run_job(use_cases, kind, body, job.id), WAIT_SECONDS)


def _row(database: FakeDatabase, job: QueuedJob) -> FakeJobRow:
    return database.jobs[job.id]


async def test_a_job_that_cant_be_claimed_runs_nothing(use_cases: UseCases) -> None:
    calls: list[ClaimedJob[JsonValue]] = []

    async def body(job: ClaimedJob[JsonValue]) -> JsonValue:
        calls.append(job)
        return None

    await run_job(use_cases, KIND, body, JobId(uuid7()))

    assert calls == []


async def test_a_returned_result_completes_the_job(
    database: FakeDatabase, use_cases: UseCases
) -> None:
    job = await _recorded(database)

    async def body(claimed: ClaimedJob[JsonValue]) -> JsonValue:
        return {"got": claimed.payload, "attempt": claimed.attempts}

    await _run(use_cases, job, body)

    row = _row(database, job)
    assert (row.status, row.attempts) == (JobStatus.SUCCEEDED, 1)
    assert row.result == {"got": {"n": 1}, "attempt": 1}


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (BodyFailedError("The test job failed."), "test_failure"),
        (RuntimeError("broken"), "unhandled_error"),
        (TimeoutError("the body's own"), "unhandled_error"),
    ],
    ids=["coded", "unhandled", "own-timeout"],
)
async def test_a_raising_body_fails_the_attempt_with_its_code(
    database: FakeDatabase, use_cases: UseCases, error: Exception, code: str
) -> None:
    job = await _recorded(database)

    async def body(_claimed: ClaimedJob[JsonValue]) -> JsonValue:
        raise error

    await _run(use_cases, job, body)

    row = _row(database, job)
    assert (row.status, row.attempts, row.error_code) == (JobStatus.QUEUED, 1, code)


async def test_a_body_past_its_time_limit_fails_with_timeout(
    database: FakeDatabase, use_cases: UseCases
) -> None:
    kind = replace(KIND, time_limit=timedelta(milliseconds=50))
    job = await _recorded(database, kind)

    async def body(_claimed: ClaimedJob[JsonValue]) -> JsonValue:
        await asyncio.sleep(WAIT_SECONDS)
        return None

    await _run(use_cases, job, body, kind)

    assert _row(database, job).error_code == "timeout"


async def test_a_payload_that_wont_decode_fails_the_attempt(
    database: FakeDatabase, use_cases: UseCases
) -> None:
    kind = replace(KIND, payload_codec=UndecodableCodec())
    job = await _recorded(database, kind)
    calls: list[ClaimedJob[JsonValue]] = []

    async def body(claimed: ClaimedJob[JsonValue]) -> JsonValue:
        calls.append(claimed)
        return None

    await _run(use_cases, job, body, kind)

    assert calls == []
    assert _row(database, job).error_code == "unhandled_error"


async def test_a_body_outlasting_several_leases_keeps_its_job(
    database: FakeDatabase, use_cases: UseCases
) -> None:
    job = await _recorded(database)

    async def body(_claimed: ClaimedJob[JsonValue]) -> JsonValue:
        await asyncio.sleep(0.7)
        return "done"

    await _run(use_cases, job, body)

    row = _row(database, job)
    assert (row.status, row.attempts) == (JobStatus.SUCCEEDED, 1)


async def test_a_lost_renewal_cancels_the_body_and_writes_nothing(
    database: FakeDatabase, use_cases: UseCases, caplog: pytest.LogCaptureFixture
) -> None:
    job = await _recorded(database)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def body(_claimed: ClaimedJob[JsonValue]) -> JsonValue:
        started.set()
        try:
            await asyncio.sleep(WAIT_SECONDS)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return None

    running = asyncio.create_task(_run(use_cases, job, body))
    await started.wait()
    # Another worker takes the job over, as after an expired lease.
    taken = replace(_row(database, job), attempts=2)
    database.jobs[job.id] = taken
    with caplog.at_level(logging.WARNING):
        await running

    assert cancelled.is_set()
    assert _row(database, job) == taken
    assert "lease_lost" in caplog.text


async def test_renewals_that_keep_failing_give_the_job_up(
    database: FakeDatabase, caplog: pytest.LogCaptureFixture
) -> None:
    job = await _recorded(database)
    use_cases = job_use_cases(
        fake_unit_of_work_factory(database),
        renewal_uow=lambda: BrokenUnitOfWork(database),
    )
    cancelled = asyncio.Event()

    async def body(_claimed: ClaimedJob[JsonValue]) -> JsonValue:
        try:
            await asyncio.sleep(WAIT_SECONDS)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return None

    with caplog.at_level(logging.WARNING):
        await _run(use_cases, job, body)

    assert cancelled.is_set()
    row = _row(database, job)
    assert (row.status, row.attempts, row.error_code) == (JobStatus.RUNNING, 1, None)
    assert "ConnectionError" in caplog.text
    assert "lease_lost" in caplog.text


async def test_a_lost_completion_is_logged(
    database: FakeDatabase, use_cases: UseCases, caplog: pytest.LogCaptureFixture
) -> None:
    job = await _recorded(database)

    async def body(_claimed: ClaimedJob[JsonValue]) -> JsonValue:
        database.jobs[job.id] = replace(_row(database, job), attempts=2)
        return "late"

    with caplog.at_level(logging.WARNING):
        await _run(use_cases, job, body)

    assert _row(database, job).status == JobStatus.RUNNING
    assert "lease_lost" in caplog.text


async def test_cancelling_the_wrapper_writes_nothing(
    database: FakeDatabase, use_cases: UseCases
) -> None:
    job = await _recorded(database)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def body(_claimed: ClaimedJob[JsonValue]) -> JsonValue:
        started.set()
        try:
            await asyncio.sleep(WAIT_SECONDS)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return None

    running = asyncio.create_task(run_job(use_cases, KIND, body, job.id))
    await started.wait()
    _ = running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert cancelled.is_set()
    row = _row(database, job)
    assert (row.status, row.attempts, row.error_code) == (JobStatus.RUNNING, 1, None)


async def test_taskiqs_backstop_stops_the_renewal_too(database: FakeDatabase) -> None:
    job = await _recorded(database)
    renewals: list[FakeUnitOfWork] = []

    def renewal_uow() -> FakeUnitOfWork:
        uow = FakeUnitOfWork(database)
        renewals.append(uow)
        return uow

    use_cases = job_use_cases(
        fake_unit_of_work_factory(database), renewal_uow=renewal_uow
    )

    async def body(_claimed: ClaimedJob[JsonValue]) -> JsonValue:
        await asyncio.sleep(WAIT_SECONDS)
        return None

    # Taskiq cancels through anyio, which cancels again on every loop turn.
    with pytest.raises(TimeoutError), anyio.fail_after(0.3):
        await run_job(use_cases, KIND, body, job.id)
    after_backstop = len(renewals)
    await asyncio.sleep(KIND.lease.total_seconds() * 3)

    assert len(renewals) == after_backstop
    wrapper_tasks = {"_run_body", "_renew_until_lost"}
    leftovers = [
        task
        for task in asyncio.all_tasks()
        if getattr(task.get_coro(), "__name__", None) in wrapper_tasks
    ]
    assert leftovers == []
    row = _row(database, job)
    assert (row.status, row.attempts, row.error_code) == (JobStatus.RUNNING, 1, None)


async def test_an_outcome_write_that_raises_propagates_and_leaves_the_row(
    database: FakeDatabase,
) -> None:
    job = await _recorded(database)
    working = fake_unit_of_work_factory(database)
    use_cases = replace(
        job_use_cases(working),
        complete_job=CompleteJob(lambda: BrokenUnitOfWork(database)),
    )

    async def body(_claimed: ClaimedJob[JsonValue]) -> JsonValue:
        return "done"

    with pytest.raises(ConnectionError):
        await run_job(use_cases, KIND, body, job.id)

    row = _row(database, job)
    assert (row.status, row.attempts) == (JobStatus.RUNNING, 1)
