"""
Check the e2e containers run jobs: one dispatched, and one only the sweeper finds.

Run it in the test worker's container, where the stack's settings are:
`make containers.e2e.job.run`. It records a test job, which is dispatched at
once, and inserts another in SQL as if its dispatch had been lost 10 minutes
ago, which only the scheduler's sweeper recovers, within a minute. It exits 0
once both have succeeded, and 1 if they haven't within 2 minutes.
"""

import asyncio
import sys
import time
from datetime import timedelta

from sqlalchemy import select

from tally_aloft.adapters.outbound.postgres.tables.jobs import JobRecord
from tally_aloft.adapters.outbound.postgres.unit_of_work import unit_of_work_factory
from tally_aloft.adapters.outbound.taskiq.job_queue import TaskiqJobQueue
from tally_aloft.application.shared.jobs.kinds import JobStatus
from tally_aloft.bootstrap.resources import open_resources
from tally_aloft.bootstrap.settings import load_settings
from tally_aloft.domain.shared.ids import PilotId
from tests.support.job_rows import insert_jobs
from tests.support.jobs import ECHO, RecordTestJob

_WAIT_SECONDS = 120
_POLL_SECONDS = 0.5
# Older than the stale threshold, so the next sweep takes it.
_LOST_DISPATCH = timedelta(minutes=10)


async def main() -> int:
    """Start both jobs, then report their statuses until both are done."""
    async with open_resources(load_settings()) as resources:
        uow = unit_of_work_factory(resources.engine)
        record = RecordTestJob(uow, TaskiqJobQueue(resources.broker))
        dispatched = (await record(PilotId.new(), {"from": "e2e"})).id
        [stale] = await insert_jobs(
            resources.engine,
            ECHO,
            dispatched_ago=_LOST_DISPATCH,
            payload={"from": "e2e, never dispatched"},
        )
        jobs = {dispatched: "recorded and dispatched", stale: "never dispatched"}
        for job_id, how in jobs.items():
            print(f"Started job {job_id.value} ({ECHO.name}, {how})")  # noqa: T201  # A command's output.
        deadline = time.monotonic() + _WAIT_SECONDS
        while time.monotonic() < deadline:
            async with uow() as unit:
                rows = await unit.session.execute(
                    select(JobRecord.id, JobRecord.status).where(
                        JobRecord.id.in_([job_id.value for job_id in jobs])
                    )
                )
                statuses = {row.id: row.status for row in rows}
            done = {JobStatus.SUCCEEDED, JobStatus.FAILED}
            if len(statuses) == len(jobs) and set(statuses.values()) <= done:
                for job_id, status in statuses.items():
                    print(f"Job {job_id} {status}")  # noqa: T201  # A command's output.
                succeeded = set(statuses.values()) == {JobStatus.SUCCEEDED}
                return 0 if succeeded else 1
            await asyncio.sleep(_POLL_SECONDS)
    print(f"The jobs didn't finish within {_WAIT_SECONDS} seconds")  # noqa: T201  # A command's output.
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
