from datetime import timedelta
from typing import TYPE_CHECKING

from tally_aloft.application.shared.jobs.claim_job import ClaimJob
from tally_aloft.application.shared.jobs.complete_job import CompleteJob
from tally_aloft.application.shared.jobs.fail_job_attempt import FailJobAttempt
from tally_aloft.application.shared.jobs.kinds import JobKind, JobStatus
from tally_aloft.application.shared.jobs.renew_job_lease import RenewJobLease
from tally_aloft.domain.shared.ids import PilotId
from tests.fakes.clock import FakeClock
from tests.fakes.database import FakeDatabase
from tests.fakes.unit_of_work import FakeUnitOfWork, fake_unit_of_work_factory
from tests.support.codecs import JSON
from tests.support.counting import CountingFactory

if TYPE_CHECKING:
    from tally_aloft.application.shared.jobs.kinds import QueuedJob

KIND = JobKind(
    name="test.lifecycle",
    payload_codec=JSON,
    result_codec=JSON,
    lease=timedelta(seconds=30),
)


async def _recorded(database: FakeDatabase) -> QueuedJob:
    async with FakeUnitOfWork(database) as uow:
        return await uow.jobs.record(PilotId.new(), KIND, {"n": 1})


async def test_claim_uses_one_unit_of_work_and_the_kinds_lease() -> None:
    clock = FakeClock()
    database = FakeDatabase(clock)
    job = await _recorded(database)
    factory = CountingFactory(fake_unit_of_work_factory(database))

    claimed = await ClaimJob(factory)(job.id, KIND)

    assert claimed is not None
    assert factory.made == 1
    assert database.jobs[job.id].lease_until == clock.now() + KIND.lease


async def test_renew_complete_and_fail_each_use_one_unit_of_work() -> None:
    clock = FakeClock()
    database = FakeDatabase(clock)
    job = await _recorded(database)
    _ = await ClaimJob(CountingFactory(fake_unit_of_work_factory(database)))(
        job.id, KIND
    )
    factory = CountingFactory(fake_unit_of_work_factory(database))

    clock.advance(timedelta(seconds=10))
    assert await RenewJobLease(factory)(job.id, 1, KIND.lease)
    assert database.jobs[job.id].lease_until == clock.now() + KIND.lease
    assert await FailJobAttempt(factory)(job.id, 1, "boom")
    _ = await ClaimJob(factory)(job.id, KIND)
    assert await CompleteJob(factory)(job.id, 2, "done")

    assert factory.made == 4
    row = database.jobs[job.id]
    assert (row.status, row.result, row.error_code) == (
        JobStatus.SUCCEEDED,
        "done",
        None,
    )


async def test_a_lost_claim_reports_false_through_each_use_case() -> None:
    database = FakeDatabase(FakeClock())
    job = await _recorded(database)
    factory = CountingFactory(fake_unit_of_work_factory(database))
    _ = await ClaimJob(factory)(job.id, KIND)

    assert not await RenewJobLease(factory)(job.id, 7, KIND.lease)
    assert not await CompleteJob(factory)(job.id, 7, "late")
    assert not await FailJobAttempt(factory)(job.id, 7, "late")
    assert database.jobs[job.id].status == JobStatus.RUNNING


async def test_overlapping_fake_units_of_work_keep_each_others_commits() -> None:
    database = FakeDatabase(FakeClock())
    first = await _recorded(database)

    async with FakeUnitOfWork(database) as outer:
        second = await outer.jobs.record(PilotId.new(), KIND, {"n": 2})
        async with FakeUnitOfWork(database) as inner:
            _ = await inner.job_runner.claim(first.id, KIND.name, KIND.lease)

    assert database.jobs[first.id].status == JobStatus.RUNNING
    assert database.jobs[second.id].status == JobStatus.QUEUED
