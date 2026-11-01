"""
Job rows written straight to Postgres, in states and ages no use case makes.

The sweeper's tests and the e2e job check use these to make stale, expired
or exhausted jobs. They run as the application role, which may insert and
update jobs. This module runs inside the `dev` image, so it imports only
`tally_aloft` and the runtime packages.
"""

import json
from datetime import timedelta
from typing import TYPE_CHECKING, cast

from sqlalchemy import text

from tally_aloft.application.shared.jobs.kinds import JobId, JobStatus

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncEngine

    from tally_aloft.application.shared.codec import JsonValue

# Times are relative to the database's `now()`, the clock the sweeper reads.
_INSERT = text(
    """
    INSERT INTO jobs (
        pilot_id, kind, status, attempts, max_attempts, payload,
        lease_until, last_dispatched_at
    )
    SELECT
        uuidv7(), :kind, :status, :attempts, :max_attempts,
        CAST(:payload AS jsonb),
        now() + CAST(:lease_ends_in AS interval),
        now() - CAST(:dispatched_ago AS interval)
    FROM generate_series(1, :count)
    RETURNING id
    """
)
_BACKDATE = text(
    """
    UPDATE jobs SET
        last_dispatched_at = COALESCE(
            now() - CAST(:dispatched_ago AS interval), last_dispatched_at
        ),
        lease_until = COALESCE(now() + CAST(:lease_ends_in AS interval), lease_until)
    WHERE id = ANY(:ids)
    """
)


async def insert_jobs(  # noqa: PLR0913  # Each argument is a column a test sets.
    engine: AsyncEngine,
    kind_name: str,
    count: int = 1,
    *,
    status: JobStatus = JobStatus.QUEUED,
    attempts: int = 0,
    max_attempts: int = 5,
    dispatched_ago: timedelta = timedelta(0),
    lease_ends_in: timedelta | None = None,
    payload: JsonValue = None,
) -> list[JobId]:
    """
    Insert `count` jobs of `kind_name`, and return their IDs.

    Each was last dispatched `dispatched_ago` before now. `lease_ends_in`,
    negative for a lease that has expired, sets `lease_until`; `None` leaves
    the job unleased.
    """
    async with engine.begin() as connection:
        result = await connection.execute(
            _INSERT,
            {
                "kind": kind_name,
                "status": status,
                "attempts": attempts,
                "max_attempts": max_attempts,
                "payload": json.dumps(payload),
                "lease_ends_in": lease_ends_in,
                "dispatched_ago": dispatched_ago,
                "count": count,
            },
        )
        ids = cast("list[UUID]", result.scalars().all())
    return [JobId(job_id) for job_id in ids]


async def backdate(
    engine: AsyncEngine,
    ids: Iterable[JobId],
    *,
    dispatched_ago: timedelta | None = None,
    lease_ends_in: timedelta | None = None,
) -> None:
    """Set the jobs' last dispatch, or lease end, relative to now; `None` keeps it."""
    async with engine.begin() as connection:
        _ = await connection.execute(
            _BACKDATE,
            {
                "ids": [job_id.value for job_id in ids],
                "dispatched_ago": dispatched_ago,
                "lease_ends_in": lease_ends_in,
            },
        )
