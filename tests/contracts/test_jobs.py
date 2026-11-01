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
    ATTEMPTS_EXHAUSTED_CODE,
    UNKNOWN_KIND_CODE,
    FailedJob,
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
# A kind the sweep tests leave out of the registered kinds.
OTHER_KIND = JobKind(name="test.contract_other", payload_codec=JSON, result_codec=JSON)
REGISTERED = frozenset({KIND.name})
STALE_AFTER = timedelta(minutes=5)
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
    # Sets a row's last dispatch this long before the store's `now()`.
    age: Callable[[JobId, timedelta], Awaitable[None]]


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

    async def age(job_id: JobId, dispatched_ago: timedelta) -> None:
        row = database.jobs[job_id]
        dispatched = clock.now() - dispatched_ago
        database.jobs[job_id] = replace(row, last_dispatched_at=dispatched)

    return JobsCase(fake_unit_of_work_factory(database), read, expire_lease, force, age)


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

    async def age(job_id: JobId, dispatched_ago: timedelta) -> None:
        await update(
            "UPDATE jobs SET last_dispatched_at = now() - CAST(:ago AS interval) "
            "WHERE id = :id",
            id=job_id.value,
            ago=dispatched_ago,
        )

    yield JobsCase(unit_of_work_factory(engine), read, expire_lease, force, age)
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


async def test_success_after_a_failed_attempt_clears_its_code(case: JobsCase) -> None:
    job = await _record(case)
    _ = await _claim(case, job)
    async with case.uow() as uow:
        _ = await uow.job_runner.fail(job.id, 1, "boom")
    _ = await _claim(case, job)

    async with case.uow() as uow:
        assert await uow.job_runner.complete(job.id, 2, "done")

    state = await _state(case, job)
    assert (state.status, state.error_code) == (JobStatus.SUCCEEDED, None)


async def _fail_unrunnable(
    case: JobsCase, kind_names: frozenset[str] = REGISTERED, limit: int = 100
) -> list[FailedJob]:
    async with case.uow() as uow:
        return await uow.job_runner.fail_unrunnable(kind_names, limit)


async def _take_stale(case: JobsCase, limit: int = 100) -> list[QueuedJob]:
    async with case.uow() as uow:
        return await uow.job_runner.take_stale(STALE_AFTER, limit)


async def _running(
    case: JobsCase, kind: JobKind[JsonValue, JsonValue] = KIND
) -> QueuedJob:
    """Record a job and claim it, so it is `running` with a valid lease."""
    job = await _record(case, kind)
    async with case.uow() as uow:
        assert await uow.job_runner.claim(job.id, kind.name, KIND.lease) is not None
    return job


async def _outcome(case: JobsCase, job: QueuedJob) -> tuple[str, str | None, bool]:
    state = await _state(case, job)
    return (state.status, state.error_code, state.leased)


def _by_id(jobs: list[FailedJob]) -> list[FailedJob]:
    return sorted(jobs, key=lambda job: job.id.value)


async def test_exhausted_queued_and_expired_running_jobs_fail(case: JobsCase) -> None:
    queued = await _record(case)
    await case.force(queued.id, JobStatus.QUEUED, KIND.max_attempts)
    running = await _running(case)
    await case.force(running.id, JobStatus.RUNNING, KIND.max_attempts)
    await case.expire_lease(running.id)

    failed = await _fail_unrunnable(case)

    assert _by_id(failed) == _by_id(
        [
            FailedJob(queued.id, KIND.name, ATTEMPTS_EXHAUSTED_CODE),
            FailedJob(running.id, KIND.name, ATTEMPTS_EXHAUSTED_CODE),
        ]
    )
    expected = (JobStatus.FAILED, ATTEMPTS_EXHAUSTED_CODE, False)
    assert await _outcome(case, queued) == expected
    assert await _outcome(case, running) == expected


async def test_a_last_attempt_with_a_valid_lease_is_left_running(
    case: JobsCase,
) -> None:
    job = await _running(case)
    await case.force(job.id, JobStatus.RUNNING, KIND.max_attempts)
    before = await _state(case, job)

    assert await _fail_unrunnable(case) == []
    assert await _state(case, job) == before


async def test_jobs_of_an_unknown_kind_fail(case: JobsCase) -> None:
    queued = await _record(case, OTHER_KIND)
    running = await _running(case, OTHER_KIND)
    exhausted = await _record(case, OTHER_KIND)
    await case.force(exhausted.id, JobStatus.QUEUED, OTHER_KIND.max_attempts)

    failed = await _fail_unrunnable(case)

    assert {job.id for job in failed} == {queued.id, running.id, exhausted.id}
    assert {job.error_code for job in failed} == {UNKNOWN_KIND_CODE}
    for job in (queued, running, exhausted):
        assert await _outcome(case, job) == (
            JobStatus.FAILED,
            UNKNOWN_KIND_CODE,
            False,
        )


async def test_with_no_registered_kinds_every_active_job_is_unknown(
    case: JobsCase,
) -> None:
    queued = await _record(case)
    running = await _running(case)

    failed = await _fail_unrunnable(case, frozenset())

    assert {job.id for job in failed} == {queued.id, running.id}
    assert {job.error_code for job in failed} == {UNKNOWN_KIND_CODE}


async def test_runnable_and_finished_jobs_are_not_failed(case: JobsCase) -> None:
    queued = await _record(case)
    expired = await _running(case)
    await case.expire_lease(expired.id)
    succeeded = await _running(case)
    async with case.uow() as uow:
        _ = await uow.job_runner.complete(succeeded.id, 1, "done")
    finished = await _record(case, OTHER_KIND)
    await case.force(finished.id, JobStatus.FAILED, 1)
    jobs = (queued, expired, succeeded, finished)
    before = [await _state(case, job) for job in jobs]

    assert await _fail_unrunnable(case) == []
    assert [await _state(case, job) for job in jobs] == before


async def test_fail_unrunnable_stops_at_its_limit(case: JobsCase) -> None:
    jobs = [await _record(case, OTHER_KIND) for _ in range(3)]

    failed = await _fail_unrunnable(case, limit=2)

    assert len(failed) == 2
    statuses = sorted([(await _state(case, job)).status for job in jobs])
    assert statuses == [JobStatus.FAILED, JobStatus.FAILED, JobStatus.QUEUED]


async def test_a_stale_queued_job_is_taken_and_a_fresh_one_left(
    case: JobsCase,
) -> None:
    stale = await _record(case)
    await case.age(stale.id, STALE_AFTER + timedelta(seconds=30))
    fresh = await _record(case)
    await case.age(fresh.id, STALE_AFTER - timedelta(seconds=30))

    assert await _take_stale(case) == [stale]
    assert (await _state(case, fresh)).status == JobStatus.QUEUED


async def test_an_expired_running_job_is_taken_once_its_dispatch_is_stale(
    case: JobsCase,
) -> None:
    stale = await _running(case)
    await case.expire_lease(stale.id)
    await case.age(stale.id, STALE_AFTER + timedelta(seconds=30))
    recent = await _running(case)
    await case.expire_lease(recent.id)
    await case.age(recent.id, STALE_AFTER - timedelta(seconds=30))
    leased = await _running(case)
    await case.age(leased.id, STALE_AFTER + timedelta(seconds=30))

    assert await _take_stale(case) == [stale]
    assert (await _state(case, stale)).status == JobStatus.RUNNING


async def test_an_exhausted_job_is_not_taken(case: JobsCase) -> None:
    job = await _record(case)
    await case.force(job.id, JobStatus.QUEUED, KIND.max_attempts)
    await case.age(job.id, STALE_AFTER + timedelta(seconds=30))

    assert await _take_stale(case) == []


async def test_a_taken_job_is_not_taken_again(case: JobsCase) -> None:
    job = await _record(case)
    await case.age(job.id, STALE_AFTER + timedelta(seconds=30))

    first = await _take_stale(case)
    second = await _take_stale(case)

    assert (first, second) == ([job], [])


async def test_take_stale_takes_the_oldest_dispatches_up_to_its_limit(
    case: JobsCase,
) -> None:
    jobs = [await _record(case) for _ in range(3)]
    for minutes, job in zip((8, 10, 6), jobs, strict=True):
        await case.age(job.id, timedelta(minutes=minutes))

    taken = await _take_stale(case, limit=2)

    assert {job.id for job in taken} == {jobs[1].id, jobs[0].id}
