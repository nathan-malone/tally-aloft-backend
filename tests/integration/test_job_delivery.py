"""
Job delivery against real Postgres: duplicates, lost leases, and the worker.

Concurrency is coordinated with events, never sleeps, so each case is the same
on every run. The last tests run the test worker's own tasks in this process.
"""

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from tally_aloft.adapters.inbound.jobs.run_job import run_job
from tally_aloft.adapters.outbound.postgres.tables.jobs import JobRecord
from tally_aloft.adapters.outbound.postgres.unit_of_work import unit_of_work_factory
from tally_aloft.adapters.outbound.redis.client import build_client
from tally_aloft.adapters.outbound.taskiq.job_queue import TaskiqJobQueue
from tally_aloft.application.shared.jobs.kinds import JobKind, JobStatus
from tally_aloft.bootstrap.broker import CONSUMER_GROUP, STREAM
from tally_aloft.bootstrap.settings import Settings
from tally_aloft.bootstrap.worker import create_worker
from tally_aloft.domain.shared.ids import PilotId
from tests.support.codecs import JSON
from tests.support.in_process_worker import eventually, running_worker
from tests.support.jobs import ECHO, FAIL, OVERRUN, RecordTestJob
from tests.support.postgres import connected
from tests.support.tasks import TEST_TASKS
from tests.support.use_cases import job_use_cases

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from datetime import datetime

    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine

    from tally_aloft.adapters.outbound.postgres.unit_of_work import PostgresUnitOfWork
    from tally_aloft.application.registry import UseCases
    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.jobs.kinds import ClaimedJob, QueuedJob
    from tests.support.postgres import PostgresDatabase

pytestmark = pytest.mark.container

type Kind = JobKind[JsonValue, JsonValue]

# A long lease, so no renewal fires while a test expires the lease by hand.
HELD: Kind = JobKind(
    name="test.held",
    payload_codec=JSON,
    result_codec=JSON,
    lease=timedelta(seconds=60),
    time_limit=timedelta(seconds=30),
)
WAIT_SECONDS = 20


@pytest.fixture
async def engine(database: PostgresDatabase) -> AsyncGenerator[AsyncEngine]:
    engine = create_async_engine(database.app_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def make_uow(engine: AsyncEngine) -> Callable[[], PostgresUnitOfWork]:
    return unit_of_work_factory(engine)


@pytest.fixture
def use_cases(make_uow: Callable[[], PostgresUnitOfWork]) -> UseCases:
    return job_use_cases(make_uow)


async def _recorded(
    make_uow: Callable[[], PostgresUnitOfWork], kind: Kind, payload: JsonValue = None
) -> QueuedJob:
    async with make_uow() as uow:
        return await uow.jobs.record(PilotId.new(), kind, payload)


async def _row(database: PostgresDatabase, job: QueuedJob) -> JobRecordView:
    async with connected(database.app_url) as connection:
        result = await connection.execute(
            select(JobRecord.__table__).where(JobRecord.id == job.id.value)
        )
        row = result.one()
    return JobRecordView(
        status=row.status,
        attempts=row.attempts,
        result=cast("JsonValue", row.result),
        error_code=row.error_code,
        updated_at=row.updated_at,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class JobRecordView:
    """The columns a test checks, read on a connection of its own."""

    status: str
    attempts: int
    result: JsonValue
    error_code: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Stack:
    """The test worker's settings, and the database its jobs live in."""

    settings: Settings
    database: PostgresDatabase
    make_uow: Callable[[], PostgresUnitOfWork]


async def _set(database: PostgresDatabase, job: QueuedJob, assignments: str) -> None:
    async with connected(database.app_url) as connection:
        _ = await connection.execute(
            text(f"UPDATE jobs SET {assignments} WHERE id = :id"),  # noqa: S608  # The assignments are this file's own literals.
            {"id": job.id.value},
        )
        await connection.commit()


class HeldBody:
    """A body that runs until the test releases it, counting its runs."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.runs = 0

    async def __call__(self, claimed: ClaimedJob[JsonValue]) -> JsonValue:
        self.runs += 1
        self.started.set()
        await self.release.wait()
        return {"attempt": claimed.attempts}


async def test_two_deliveries_at_once_run_the_body_once(
    database: PostgresDatabase,
    make_uow: Callable[[], PostgresUnitOfWork],
    use_cases: UseCases,
) -> None:
    job = await _recorded(make_uow, HELD)
    body = HeldBody()

    deliveries = [
        asyncio.create_task(run_job(use_cases, HELD, body, job.id)) for _ in range(2)
    ]
    await asyncio.wait_for(body.started.wait(), WAIT_SECONDS)
    done, _ = await asyncio.wait(
        deliveries, timeout=WAIT_SECONDS, return_when=asyncio.FIRST_COMPLETED
    )
    during = await _row(database, job)
    body.release.set()
    await asyncio.wait_for(asyncio.gather(*deliveries), WAIT_SECONDS)

    assert len(done) == 1
    assert (during.status, during.attempts) == (JobStatus.RUNNING, 1)
    assert body.runs == 1
    after = await _row(database, job)
    assert (after.status, after.attempts) == (JobStatus.SUCCEEDED, 1)


async def test_a_late_delivery_of_a_finished_job_changes_nothing(
    database: PostgresDatabase,
    make_uow: Callable[[], PostgresUnitOfWork],
    use_cases: UseCases,
) -> None:
    job = await _recorded(make_uow, HELD)
    body = HeldBody()
    body.release.set()
    await run_job(use_cases, HELD, body, job.id)
    finished = await _row(database, job)

    await run_job(use_cases, HELD, body, job.id)

    assert body.runs == 1
    again = await _row(database, job)
    assert (again.status, again.attempts, again.updated_at) == (
        JobStatus.SUCCEEDED,
        1,
        finished.updated_at,
    )


async def test_a_worker_that_lost_its_lease_writes_nothing(
    database: PostgresDatabase,
    make_uow: Callable[[], PostgresUnitOfWork],
    use_cases: UseCases,
    caplog: pytest.LogCaptureFixture,
) -> None:
    job = await _recorded(make_uow, HELD)
    first, second = HeldBody(), HeldBody()
    first_run = asyncio.create_task(run_job(use_cases, HELD, first, job.id))
    await asyncio.wait_for(first.started.wait(), WAIT_SECONDS)

    await _set(database, job, "lease_until = now() - interval '1 second'")
    second_run = asyncio.create_task(run_job(use_cases, HELD, second, job.id))
    await asyncio.wait_for(second.started.wait(), WAIT_SECONDS)
    with caplog.at_level(logging.WARNING):
        first.release.set()
        await asyncio.wait_for(first_run, WAIT_SECONDS)
    between = await _row(database, job)
    second.release.set()
    await asyncio.wait_for(second_run, WAIT_SECONDS)

    # With a 60-second lease no renewal ran, so the first run's completion
    # is what found the job lost.
    assert "lease_lost: job" in caplog.text
    assert "attempt 1 lost its lease" in caplog.text
    assert (between.status, between.attempts) == (JobStatus.RUNNING, 2)
    after = await _row(database, job)
    assert (after.status, after.attempts, after.result) == (
        JobStatus.SUCCEEDED,
        2,
        {"attempt": 2},
    )


async def test_a_long_body_keeps_its_lease_on_sessions_of_its_own(
    database: PostgresDatabase,
    make_uow: Callable[[], PostgresUnitOfWork],
) -> None:
    kind = replace(HELD, lease=timedelta(seconds=1))
    job = await _recorded(make_uow, kind)
    renewals: list[tuple[PostgresUnitOfWork, asyncio.Task[object] | None]] = []

    def renewal_uow() -> PostgresUnitOfWork:
        uow = make_uow()
        renewals.append((uow, asyncio.current_task()))
        return uow

    use_cases = job_use_cases(make_uow, renewal_uow=renewal_uow)
    body_uows: list[PostgresUnitOfWork] = []
    body_tasks: list[asyncio.Task[object] | None] = []
    claims_during: list[object] = []

    async def body(_claimed: ClaimedJob[JsonValue]) -> JsonValue:
        body_tasks.append(asyncio.current_task())
        async with make_uow() as uow:
            body_uows.append(uow)
            for _ in range(3):
                await asyncio.sleep(1)
                claims_during.append(await use_cases.claim_job(job.id, kind))
        return "done"

    await asyncio.wait_for(run_job(use_cases, kind, body, job.id), WAIT_SECONDS)

    assert len(renewals) >= 2
    assert len({id(uow) for uow, _ in renewals}) == len(renewals)
    assert all(uow is not body_uows[0] for uow, _ in renewals)
    assert all(task is not body_tasks[0] for _, task in renewals)
    assert claims_during == [None, None, None]
    after = await _row(database, job)
    assert (after.status, after.attempts) == (JobStatus.SUCCEEDED, 1)


async def test_a_queued_job_at_max_attempts_isnt_run(
    database: PostgresDatabase,
    make_uow: Callable[[], PostgresUnitOfWork],
    use_cases: UseCases,
) -> None:
    job = await _recorded(make_uow, HELD)
    await _set(database, job, f"attempts = {HELD.max_attempts}")
    body = HeldBody()
    body.release.set()

    await run_job(use_cases, HELD, body, job.id)

    assert body.runs == 0
    row = await _row(database, job)
    assert (row.status, row.attempts) == (JobStatus.QUEUED, HELD.max_attempts)


@pytest.fixture
async def valkey(valkey_url: str) -> AsyncGenerator[Redis]:
    client = build_client(valkey_url)
    yield client
    await client.aclose()


@pytest.fixture
def stack(
    database: PostgresDatabase,
    valkey_url: str,
    make_uow: Callable[[], PostgresUnitOfWork],
) -> Stack:
    settings = Settings.for_tests(
        database_url=database.app_url, valkey_url=valkey_url, broker_idle_timeout=10
    )
    return Stack(settings, database, make_uow)


async def _pending(valkey: Redis) -> int:
    summary = await valkey.xpending(STREAM, CONSUMER_GROUP)
    return int(summary["pending"])


async def _worker_runs(
    stack: Stack,
    kind: Kind,
    payload: JsonValue,
    *,
    until: Callable[[JobRecordView], bool],
) -> JobRecordView:
    """Record and dispatch a job, and run the test worker until `until` holds."""
    broker = create_worker(stack.settings, TEST_TASKS)
    record = RecordTestJob(stack.make_uow, TaskiqJobQueue(broker))
    async with running_worker(broker):
        job = await record(PilotId.new(), payload, kind=kind)

        async def reached() -> bool:
            return until(await _row(stack.database, job))

        await eventually(reached)
    return await _row(stack.database, job)


async def test_the_worker_runs_a_recorded_job_once(stack: Stack, valkey: Redis) -> None:
    row = await _worker_runs(
        stack,
        ECHO,
        {"n": 1},
        until=lambda row: row.status == JobStatus.SUCCEEDED,
    )

    assert (row.attempts, row.result) == (1, {"echo": {"n": 1}})
    assert await _pending(valkey) == 0


@pytest.mark.parametrize(
    ("payload", "code"),
    [("coded", "test_failure"), ("unhandled", "unhandled_error")],
)
async def test_a_failed_attempt_is_queued_with_its_code_and_acknowledged(
    stack: Stack, valkey: Redis, payload: str, code: str
) -> None:
    row = await _worker_runs(
        stack,
        FAIL,
        payload,
        until=lambda row: row.error_code is not None,
    )

    assert (row.status, row.attempts, row.error_code) == (JobStatus.QUEUED, 1, code)
    await eventually(lambda: _none_pending(valkey))


async def _none_pending(valkey: Redis) -> bool:
    return await _pending(valkey) == 0


async def test_the_last_attempt_fails_the_job(stack: Stack) -> None:
    database = stack.database
    broker = create_worker(stack.settings, TEST_TASKS)
    queue = TaskiqJobQueue(broker)
    record = RecordTestJob(stack.make_uow, queue)
    async with running_worker(broker):
        job = await record(PilotId.new(), "coded", kind=FAIL)
        await eventually(lambda: _attempts_failed(database, job, 1))
        await queue.dispatch(job.id, job.kind_name)
        await eventually(lambda: _attempts_failed(database, job, FAIL.max_attempts))

    row = await _row(database, job)
    assert (row.status, row.error_code) == (JobStatus.FAILED, "test_failure")


async def _attempts_failed(
    database: PostgresDatabase, job: QueuedJob, attempts: int
) -> bool:
    row = await _row(database, job)
    return row.attempts == attempts and row.status != JobStatus.RUNNING


async def test_a_body_past_its_time_limit_fails_before_the_backstop(
    stack: Stack,
) -> None:
    row = await _worker_runs(
        stack,
        OVERRUN,
        None,
        until=lambda row: row.status != JobStatus.RUNNING and row.attempts == 1,
    )

    # Taskiq's backstop would cancel the wrapper and leave the row running.
    assert (row.status, row.error_code) == (JobStatus.QUEUED, "timeout")
