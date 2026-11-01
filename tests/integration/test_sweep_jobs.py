"""
The sweeper on real Postgres: what it fails, what it re-dispatches, and once.

Time is simulated by backdating rows in SQL, against the database's `now()`,
so no test waits out a threshold. Dispatches go to a `FakeJobQueue`, which
records each one, except in the last test, which runs the sweeper task in the
test worker on real Valkey.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from tally_aloft.adapters.inbound.jobs.sweeper import SWEEP_TASK
from tally_aloft.adapters.outbound.postgres.tables.jobs import JobRecord
from tally_aloft.adapters.outbound.postgres.unit_of_work import unit_of_work_factory
from tally_aloft.application.shared.jobs.claim_job import ClaimJob
from tally_aloft.application.shared.jobs.kinds import (
    ATTEMPTS_EXHAUSTED_CODE,
    UNKNOWN_KIND_CODE,
    JobKind,
    JobStatus,
)
from tally_aloft.application.shared.jobs.sweep_jobs import SweepJobs
from tally_aloft.bootstrap.settings import Settings
from tally_aloft.bootstrap.worker import create_worker
from tally_aloft.domain.shared.ids import PilotId
from tests.fakes.job_queue import FakeJobQueue
from tests.support.codecs import JSON
from tests.support.in_process_worker import eventually, kick, running_worker
from tests.support.job_rows import backdate, insert_jobs
from tests.support.jobs import ECHO, TEST_IDLE_TIMEOUT, RecordTestJob
from tests.support.postgres import connected
from tests.support.tasks import TEST_TASKS

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncEngine

    from tally_aloft.application.shared.jobs.kinds import JobId
    from tests.support.postgres import PostgresDatabase

pytestmark = pytest.mark.container

# The default threshold, so the timing tests check the shipped setting.
STALE_AFTER = timedelta(seconds=Settings.for_tests().job_stale_after)
STALE = STALE_AFTER + timedelta(minutes=1)
FRESH = STALE_AFTER - timedelta(minutes=1)
EXPIRED = timedelta(seconds=-1)
# A kind no worker registers.
REMOVED = JobKind(name="test.removed", payload_codec=JSON, result_codec=JSON)


@dataclass(frozen=True, slots=True)
class Row:
    """What the tests check of a job row."""

    status: str
    attempts: int
    error_code: str | None
    # Whether `last_dispatched_at` is within the last minute.
    just_dispatched: bool


@pytest.fixture
async def engine(database: PostgresDatabase) -> AsyncGenerator[AsyncEngine]:
    engine = create_async_engine(database.app_url)
    yield engine
    await engine.dispose()


def _sweep(
    engine: AsyncEngine, queue: FakeJobQueue, *, batch_size: int = 500
) -> SweepJobs:
    return SweepJobs(
        uow=unit_of_work_factory(engine),
        queue=queue,
        kind_names=frozenset({ECHO.name}),
        stale_after=STALE_AFTER,
        batch_size=batch_size,
    )


async def _row(engine: AsyncEngine, job_id: JobId) -> Row:
    statement = select(
        JobRecord.status,
        JobRecord.attempts,
        JobRecord.error_code,
        JobRecord.last_dispatched_at > func.now() - timedelta(minutes=1),
    ).where(JobRecord.id == job_id.value)
    async with engine.connect() as connection:
        status, attempts, error_code, just = (await connection.execute(statement)).one()
    return Row(status, attempts, error_code, just)


def _ids(queue: FakeJobQueue) -> list[JobId]:
    return [job_id for job_id, _kind in queue.dispatched]


async def test_a_stale_queued_job_is_dispatched_once(engine: AsyncEngine) -> None:
    [job] = await insert_jobs(engine, ECHO, dispatched_ago=STALE)
    queue = FakeJobQueue()

    await _sweep(engine, queue)()

    assert queue.dispatched == [(job, ECHO.name)]
    assert (await _row(engine, job)).just_dispatched


async def test_a_fresh_queued_job_is_not_dispatched(engine: AsyncEngine) -> None:
    _ = await insert_jobs(engine, ECHO, dispatched_ago=FRESH)
    queue = FakeJobQueue()

    await _sweep(engine, queue)()

    assert queue.dispatched == []


async def test_a_job_whose_dispatch_failed_is_dispatched_after_the_threshold(
    engine: AsyncEngine,
) -> None:
    record = RecordTestJob(unit_of_work_factory(engine), FakeJobQueue(outcome="fail"))
    job = await record(PilotId.new(), {"n": 1})
    queue = FakeJobQueue()

    # Half a minute either side of the threshold, so a slow run can't cross it.
    await backdate(engine, [job.id], dispatched_ago=STALE_AFTER - timedelta(seconds=30))
    await _sweep(engine, queue)()
    before = list(queue.dispatched)
    await backdate(engine, [job.id], dispatched_ago=STALE_AFTER + timedelta(seconds=30))
    await _sweep(engine, queue)()

    assert before == []
    assert queue.dispatched == [(job.id, ECHO.name)]


async def test_an_expired_running_job_is_dispatched_once_per_threshold(
    engine: AsyncEngine,
) -> None:
    [job] = await insert_jobs(
        engine,
        ECHO,
        status=JobStatus.RUNNING,
        attempts=1,
        dispatched_ago=STALE,
        lease_ends_in=EXPIRED,
    )
    queue = FakeJobQueue()

    await _sweep(engine, queue)()
    claimed = await ClaimJob(unit_of_work_factory(engine))(job, ECHO)
    # A minute later the lease has expired again, but the last dispatch is
    # recent, so only the staleness guard stops a second dispatch.
    await backdate(
        engine, [job], dispatched_ago=timedelta(minutes=1), lease_ends_in=EXPIRED
    )
    await _sweep(engine, queue)()

    assert queue.dispatched == [(job, ECHO.name)]
    assert claimed is not None
    assert claimed.attempts == 2


async def test_a_last_attempt_with_a_valid_lease_is_left_alone(
    engine: AsyncEngine,
) -> None:
    [job] = await insert_jobs(
        engine,
        ECHO,
        status=JobStatus.RUNNING,
        attempts=ECHO.max_attempts,
        dispatched_ago=STALE,
        lease_ends_in=timedelta(minutes=5),
    )
    queue = FakeJobQueue()

    await _sweep(engine, queue)()

    assert queue.dispatched == []
    assert await _row(engine, job) == Row(
        JobStatus.RUNNING, ECHO.max_attempts, None, just_dispatched=False
    )


async def test_exhausted_jobs_are_failed_and_not_dispatched(
    engine: AsyncEngine,
) -> None:
    exhausted = {"attempts": ECHO.max_attempts, "dispatched_ago": STALE}
    [queued] = await insert_jobs(engine, ECHO, **exhausted)
    [running] = await insert_jobs(
        engine,
        ECHO,
        status=JobStatus.RUNNING,
        lease_ends_in=EXPIRED,
        **exhausted,
    )
    queue = FakeJobQueue()

    await _sweep(engine, queue)()

    assert queue.dispatched == []
    for job in (queued, running):
        row = await _row(engine, job)
        assert (row.status, row.error_code) == (
            JobStatus.FAILED,
            ATTEMPTS_EXHAUSTED_CODE,
        )


async def test_a_job_of_an_unknown_kind_is_failed_and_not_dispatched(
    engine: AsyncEngine,
) -> None:
    [job] = await insert_jobs(engine, REMOVED, dispatched_ago=STALE)
    queue = FakeJobQueue()

    await _sweep(engine, queue)()

    assert queue.dispatched == []
    row = await _row(engine, job)
    assert (row.status, row.error_code) == (JobStatus.FAILED, UNKNOWN_KIND_CODE)


async def test_one_sweep_dispatches_at_most_a_batch(engine: AsyncEngine) -> None:
    _ = await insert_jobs(engine, ECHO, 1200, dispatched_ago=STALE)
    queue = FakeJobQueue()

    await _sweep(engine, queue, batch_size=500)()

    assert len(queue.dispatched) == 500
    assert len(set(_ids(queue))) == 500


async def test_one_sweep_fails_at_most_a_batch(engine: AsyncEngine) -> None:
    _ = await insert_jobs(engine, ECHO, 1200, attempts=ECHO.max_attempts)

    await _sweep(engine, FakeJobQueue(), batch_size=500)()

    statement = select(func.count()).where(JobRecord.status == JobStatus.FAILED)
    async with engine.connect() as connection:
        assert (await connection.execute(statement)).scalar_one() == 500


async def _stale_mix(engine: AsyncEngine) -> list[JobId]:
    """Insert 50 stale `queued` jobs and 50 expired, stale `running` ones."""
    queued = await insert_jobs(engine, ECHO, 50, dispatched_ago=STALE)
    running = await insert_jobs(
        engine,
        ECHO,
        50,
        status=JobStatus.RUNNING,
        attempts=1,
        dispatched_ago=STALE,
        lease_ends_in=EXPIRED,
    )
    return queued + running


async def test_a_sweep_skips_the_rows_another_holds_locked(
    database: PostgresDatabase, engine: AsyncEngine
) -> None:
    jobs = await _stale_mix(engine)
    held = jobs[::2]
    queue = FakeJobQueue()

    async with connected(database.app_url) as locker:
        _ = await locker.execute(
            text("SELECT id FROM jobs WHERE id = ANY(:ids) FOR UPDATE"),
            {"ids": [job.value for job in held]},
        )
        await _sweep(engine, queue)()
        while_held = set(_ids(queue))
        await locker.rollback()
    await _sweep(engine, queue)()

    assert while_held == set(jobs) - set(held)
    assert sorted(_ids(queue), key=lambda job: job.value) == sorted(
        jobs, key=lambda job: job.value
    )


async def test_concurrent_and_back_to_back_sweeps_dispatch_each_job_once(
    engine: AsyncEngine,
) -> None:
    jobs = await _stale_mix(engine)
    queue = FakeJobQueue()
    sweeps = [_sweep(engine, queue, batch_size=30) for _ in range(4)]

    _ = await asyncio.gather(*(sweep() for sweep in sweeps))
    after_overlap = len(queue.dispatched)
    await sweeps[0]()

    assert after_overlap == len(jobs)
    assert sorted(_ids(queue), key=lambda job: job.value) == sorted(
        jobs, key=lambda job: job.value
    )


async def test_a_failed_dispatch_leaves_the_rest_of_the_batch_dispatched(
    engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    jobs = await insert_jobs(engine, ECHO, 3, dispatched_ago=STALE)
    queue = FakeJobQueue(failing={jobs[1]})

    with caplog.at_level(logging.WARNING):
        await _sweep(engine, queue)()

    assert set(_ids(queue)) == {jobs[0], jobs[2]}
    [record] = caplog.records
    assert str(jobs[1].value) in record.getMessage()
    assert ECHO.name in record.getMessage()


async def test_the_sweeper_task_recovers_a_job_that_was_never_dispatched(
    database: PostgresDatabase, valkey_url: str, engine: AsyncEngine
) -> None:
    settings = Settings.for_tests(
        database_url=database.app_url,
        valkey_url=valkey_url,
        broker_idle_timeout=int(TEST_IDLE_TIMEOUT.total_seconds()),
    )
    [job] = await insert_jobs(
        engine, ECHO, dispatched_ago=timedelta(minutes=10), payload={"n": 1}
    )
    broker = create_worker(settings, TEST_TASKS)

    async def succeeded() -> bool:
        return (await _row(engine, job)).status == JobStatus.SUCCEEDED

    async with running_worker(broker):
        await kick(broker, SWEEP_TASK.name)
        await eventually(succeeded)
