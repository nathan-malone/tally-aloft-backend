"""
A test job whose body fails with a database error holding its payload.

It needs an engine and the test tables, so it isn't in `TEST_JOB_TASKS`, and
the release image, which imports only runtime packages, never loads it.
"""

from typing import TYPE_CHECKING

from sqlalchemy import insert

from tally_aloft.adapters.inbound.jobs.tasks import job_task
from tally_aloft.application.shared.jobs.kinds import JobKind
from tests.support.codecs import JSON
from tests.support.jobs import TEST_BACKSTOP, TEST_LEASE, TEST_TIME_LIMIT
from tests.support.tables import telemetry_rows

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from tally_aloft.adapters.inbound.jobs.tasks import TaskDefinition
    from tally_aloft.application.registry import UseCases
    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.jobs.kinds import ClaimedJob, JobBody

# Inserts its payload as a key twice, so its body raises a unique violation.
UNIQUE_VIOLATION = JobKind(
    name="test.unique_violation",
    payload_codec=JSON,
    result_codec=JSON,
    lease=TEST_LEASE,
    time_limit=TEST_TIME_LIMIT,
)


def unique_violation_task(engine: AsyncEngine) -> TaskDefinition:
    """Return the task for `UNIQUE_VIOLATION`, whose body writes through `engine`."""

    async def body(job: ClaimedJob[JsonValue]) -> JsonValue:
        row = {"key": str(job.payload), "value": "first"}
        async with engine.begin() as connection:
            _ = await connection.execute(insert(telemetry_rows), [row, row])
        return None

    def pick(_use_cases: UseCases) -> JobBody[JsonValue, JsonValue]:
        return body

    return job_task(UNIQUE_VIOLATION, pick, backstop=TEST_BACKSTOP)
