"""
Record a test job against the running e2e containers, and wait until it succeeds.

Run it in the test worker's container, where the stack's settings are:
`make containers.e2e.job.run`. It exits 0 once the job has succeeded, and 1
if it hasn't within a minute.
"""

import asyncio
import sys
import time

from sqlalchemy import select

from tally_aloft.adapters.outbound.postgres.tables.jobs import JobRecord
from tally_aloft.adapters.outbound.postgres.unit_of_work import unit_of_work_factory
from tally_aloft.adapters.outbound.taskiq.job_queue import TaskiqJobQueue
from tally_aloft.application.shared.jobs.kinds import JobStatus
from tally_aloft.bootstrap.resources import open_resources
from tally_aloft.bootstrap.settings import load_settings
from tally_aloft.domain.shared.ids import PilotId
from tests.support.jobs import RecordTestJob

_WAIT_SECONDS = 60
_POLL_SECONDS = 0.5


async def main() -> int:
    """Record a `test.echo` job, then report its status until it is done."""
    async with open_resources(load_settings()) as resources:
        uow = unit_of_work_factory(resources.engine)
        record = RecordTestJob(uow, TaskiqJobQueue(resources.broker))
        job = await record(PilotId.new(), {"from": "e2e"})
        print(f"Recorded job {job.id.value} ({job.kind_name})")  # noqa: T201  # A command's output.
        deadline = time.monotonic() + _WAIT_SECONDS
        while time.monotonic() < deadline:
            async with uow() as unit:
                status = await unit.session.scalar(
                    select(JobRecord.status).where(JobRecord.id == job.id.value)
                )
            if status in {JobStatus.SUCCEEDED, JobStatus.FAILED}:
                print(f"Job {job.id.value} {status}")  # noqa: T201  # A command's output.
                return 0 if status == JobStatus.SUCCEEDED else 1
            await asyncio.sleep(_POLL_SECONDS)
    print(f"Job {job.id.value} didn't finish within {_WAIT_SECONDS} seconds")  # noqa: T201  # A command's output.
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
