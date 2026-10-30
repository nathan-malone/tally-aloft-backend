"""The jobs repositories, `uow.jobs` and `uow.job_runner`: fake against Postgres."""

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, cast
from uuid import uuid7

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from tally_aloft.adapters.outbound.postgres.tables.jobs import JobRecord
from tally_aloft.adapters.outbound.postgres.unit_of_work import unit_of_work_factory
from tally_aloft.application.shared.jobs.kinds import (
    JobId,
    JobKind,
    JobStatus,
    QueuedJob,
)
from tally_aloft.domain.shared.ids import PilotId
from tests.fakes.clock import FakeClock
from tests.fakes.database import FakeDatabase
from tests.fakes.unit_of_work import fake_unit_of_work_factory
from tests.support.codecs import JSON
from tests.support.postgres import connected

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.jobs.kinds import ClaimedJob
    from tally_aloft.application.shared.jobs.ports import JobRunnerUnitOfWork
    from tests.support.postgres import PostgresDatabase

KIND = JobKind(
    name="test.contract",
    payload_codec=JSON,
    result_codec=JSON,
    max_attempts=2,
    lease=timedelta(minutes=1),
)
PAYLOAD: JsonValue = {"flight": 7, "note": None}


@dataclass(frozen=True, slots=True)
class JobState:
    """What a test can see of a job row, the same way on both stores."""

    status: str
    attempts: int
    max_attempts: int
    leased: bool
    payload: JsonValue
    result: JsonValue
    error_code: str | None


@dataclass(frozen=True, slots=True)
class JobsCase:
    """A unit-of-work factory under test, and how to inspect and age its rows."""

    uow: Callable[[], JobRunnerUnitOfWork]
    read: Callable[[JobId], Awaitable[JobState | None]]
    expire_lease: Callable[[JobId], Awaitable[None]]
    # Sets a row's status and attempts directly, for states a test can't reach.
    force: Callable[[JobId, str, int], Awaitable[None]]


@pytest.fixture
def fake_case() -> JobsCase:
    clock = FakeClock()
    database = FakeDatabase(clock)

    async def read(job_id: JobId) -> JobState | None:
        row = database.jobs.get(job_id)
        if row is None:
            return None
        return JobState(
            status=row.status,
            attempts=row.attempts,
            max_attempts=row.max_attempts,
            leased=row.lease_until is not None,
            payload=row.payload,
            result=row.result,
            error_code=row.error_code,
        )

    async def expire_lease(job_id: JobId) -> None:
        row = database.jobs[job_id]
        expired = clock.now() - timedelta(seconds=1)
        database.jobs[job_id] = replace(row, lease_until=expired)

    async def force(job_id: JobId, status: str, attempts: int) -> None:
        row = database.jobs[job_id]
        database.jobs[job_id] = replace(row, status=status, attempts=attempts)

    return JobsCase(fake_unit_of_work_factory(database), read, expire_lease, force)


@pytest.fixture
async def postgres_case(database: PostgresDatabase) -> AsyncGenerator[JobsCase]:
    engine = create_async_engine(database.app_url)

    async def read(job_id: JobId) -> JobState | None:
        async with connected(database.app_url) as connection:
            result = await connection.execute(
                select(JobRecord.__table__).where(JobRecord.id == job_id.value)
            )
            row = result.one_or_none()
        if row is None:
            return None
        return JobState(
            status=row.status,
            attempts=row.attempts,
            max_attempts=row.max_attempts,
            leased=row.lease_until is not None,
            payload=cast("JsonValue", row.payload),
            result=cast("JsonValue", row.result),
            error_code=row.error_code,
        )

    async def update(statement: str, **values: object) -> None:
        async with connected(database.app_url) as connection:
            _ = await connection.execute(text(statement), values)
            await connection.commit()

    async def expire_lease(job_id: JobId) -> None:
        await update(
            "UPDATE jobs SET lease_until = now() - interval '1 second' WHERE id = :id",
            id=job_id.value,
        )

    async def force(job_id: JobId, status: str, attempts: int) -> None:
        await update(
            "UPDATE jobs SET status = :status, attempts = :attempts WHERE id = :id",
            id=job_id.value,
            status=status,
            attempts=attempts,
        )

    yield JobsCase(unit_of_work_factory(engine), read, expire_lease, force)
    await engine.dispose()


# Parametrized by fixture name, as the cache contract is.
@pytest.fixture(
    params=["fake_case", pytest.param("postgres_case", marks=pytest.mark.container)]
)
def case(request: pytest.FixtureRequest) -> JobsCase:
    return cast("JobsCase", request.getfixturevalue(cast("str", request.param)))


async def _record(
    case: JobsCase, kind: JobKind[JsonValue, JsonValue] = KIND
) -> QueuedJob:
    async with case.uow() as uow:
        return await uow.jobs.record(PilotId.new(), kind, PAYLOAD)


async def _claim(
    case: JobsCase, job: QueuedJob, kind_name: str = KIND.name
) -> ClaimedJob[JsonValue] | None:
    async with case.uow() as uow:
        return await uow.job_runner.claim(job.id, kind_name, KIND.lease)


async def _state(case: JobsCase, job: QueuedJob) -> JobState:
    state = await case.read(job.id)
    assert state is not None
    return state


async def test_record_inserts_a_queued_job(case: JobsCase) -> None:
    job = await _record(case)

    assert job.kind_name == KIND.name
    assert await _state(case, job) == JobState(
        status=JobStatus.QUEUED,
        attempts=0,
        max_attempts=2,
        leased=False,
        payload=PAYLOAD,
        result=None,
        error_code=None,
    )


async def test_a_unit_of_work_that_raises_records_nothing(case: JobsCase) -> None:
    recorded: list[QueuedJob] = []
    msg = "the use case failed"

    async def failing_use_case() -> None:
        async with case.uow() as uow:
            recorded.append(await uow.jobs.record(PilotId.new(), KIND, PAYLOAD))
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match=msg):
        await failing_use_case()

    assert await case.read(recorded[0].id) is None


async def test_a_claim_leases_the_job_and_returns_its_payload(case: JobsCase) -> None:
    job = await _record(case)

    claimed = await _claim(case, job)

    assert claimed is not None
    assert (claimed.id, claimed.kind, claimed.attempts) == (job.id, KIND.name, 1)
    assert claimed.payload == PAYLOAD
    state = await _state(case, job)
    assert (state.status, state.attempts, state.leased) == (JobStatus.RUNNING, 1, True)


async def test_a_claim_within_the_lease_gets_nothing(case: JobsCase) -> None:
    job = await _record(case)
    _ = await _claim(case, job)

    assert await _claim(case, job) is None
    assert (await _state(case, job)).attempts == 1


async def test_a_claim_after_the_lease_expires_takes_the_next_attempt(
    case: JobsCase,
) -> None:
    job = await _record(case)
    _ = await _claim(case, job)
    await case.expire_lease(job.id)

    claimed = await _claim(case, job)

    assert claimed is not None
    assert claimed.attempts == 2


async def test_an_expired_claim_at_max_attempts_is_not_taken_again(
    case: JobsCase,
) -> None:
    job = await _record(case)
    _ = await _claim(case, job)
    async with case.uow() as uow:
        _ = await uow.job_runner.fail(job.id, 1, "boom")
    _ = await _claim(case, job)
    await case.expire_lease(job.id)

    assert await _claim(case, job) is None
    assert (await _state(case, job)).attempts == 2


async def test_a_queued_job_at_max_attempts_is_not_taken(case: JobsCase) -> None:
    job = await _record(case)
    await case.force(job.id, JobStatus.QUEUED, KIND.max_attempts)

    assert await _claim(case, job) is None
    assert (await _state(case, job)).status == JobStatus.QUEUED


async def test_a_claim_under_another_kinds_name_gets_nothing(case: JobsCase) -> None:
    job = await _record(case)

    assert await _claim(case, job, kind_name="test.other") is None
    assert (await _state(case, job)).status == JobStatus.QUEUED


async def test_a_claim_of_a_missing_job_gets_nothing(case: JobsCase) -> None:
    missing = QueuedJob(JobId(uuid7()), KIND.name)

    assert await _claim(case, missing) is None


async def test_renew_extends_only_the_current_claim(case: JobsCase) -> None:
    job = await _record(case)
    _ = await _claim(case, job)
    await case.expire_lease(job.id)
    _ = await _claim(case, job)

    async with case.uow() as uow:
        stale = await uow.job_runner.renew(job.id, 1, KIND.lease)
    async with case.uow() as uow:
        current = await uow.job_runner.renew(job.id, 2, KIND.lease)

    assert (stale, current) == (False, True)


async def test_complete_stores_the_result(case: JobsCase) -> None:
    job = await _record(case)
    _ = await _claim(case, job)

    async with case.uow() as uow:
        assert await uow.job_runner.complete(job.id, 1, {"pages": 3})

    state = await _state(case, job)
    assert (state.status, state.result, state.leased) == (
        JobStatus.SUCCEEDED,
        {"pages": 3},
        False,
    )


async def test_a_stale_claim_changes_nothing(case: JobsCase) -> None:
    job = await _record(case)
    _ = await _claim(case, job)
    await case.expire_lease(job.id)
    _ = await _claim(case, job)
    before = await _state(case, job)

    async with case.uow() as uow:
        completed = await uow.job_runner.complete(job.id, 1, "late")
        failed = await uow.job_runner.fail(job.id, 1, "late")
        renewed = await uow.job_runner.renew(job.id, 1, KIND.lease)

    assert (completed, failed, renewed) == (False, False, False)
    assert await _state(case, job) == before


async def test_a_finished_job_takes_no_more_writes(case: JobsCase) -> None:
    job = await _record(case)
    _ = await _claim(case, job)
    async with case.uow() as uow:
        _ = await uow.job_runner.complete(job.id, 1, "done")

    async with case.uow() as uow:
        assert not await uow.job_runner.fail(job.id, 1, "late")
    assert await _claim(case, job) is None
    assert (await _state(case, job)).status == JobStatus.SUCCEEDED


async def test_fail_requeues_below_max_attempts_and_fails_at_it(case: JobsCase) -> None:
    job = await _record(case)
    _ = await _claim(case, job)

    async with case.uow() as uow:
        assert await uow.job_runner.fail(job.id, 1, "first")
    first = await _state(case, job)
    _ = await _claim(case, job)
    async with case.uow() as uow:
        assert await uow.job_runner.fail(job.id, 2, "second")
    second = await _state(case, job)

    assert (first.status, first.error_code, first.leased) == (
        JobStatus.QUEUED,
        "first",
        False,
    )
    assert (second.status, second.error_code) == (JobStatus.FAILED, "second")


async def test_a_json_null_payload_round_trips(case: JobsCase) -> None:
    async with case.uow() as uow:
        job = await uow.jobs.record(PilotId.new(), KIND, None)

    claimed = await _claim(case, job)

    assert claimed is not None
    assert claimed.payload is None


@pytest.mark.container
async def test_postgres_stores_a_json_null_payload_as_json(
    database: PostgresDatabase, postgres_case: JobsCase
) -> None:
    async with postgres_case.uow() as uow:
        job = await uow.jobs.record(PilotId.new(), KIND, None)

    async with connected(database.app_url) as connection:
        result = await connection.execute(
            text("SELECT jsonb_typeof(payload) FROM jobs WHERE id = :id"),
            {"id": job.id.value},
        )

    assert result.scalar_one() == "null"
