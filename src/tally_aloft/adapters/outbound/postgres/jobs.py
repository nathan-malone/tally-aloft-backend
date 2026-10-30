from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from sqlalchemy import and_, case, func, insert, or_, update

from tally_aloft.adapters.outbound.postgres.tables.jobs import JobRecord
from tally_aloft.application.shared.jobs.kinds import (
    ClaimedJob,
    JobId,
    JobStatus,
    QueuedJob,
)
from tally_aloft.domain.shared.ids import PilotId

if TYPE_CHECKING:
    from datetime import timedelta

    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession

    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.jobs.kinds import JobKind

# ORM-enabled updates would otherwise look for the rows in the session.
_NO_SYNC = {"synchronize_session": False}


@dataclass(frozen=True, slots=True)
class PostgresJobRepository:
    """The pilot-scoped `JobRepository` on one unit of work's session."""

    session: AsyncSession

    async def record[P, R](
        self, actor: PilotId, kind: JobKind[P, R], payload: P
    ) -> QueuedJob:
        """Insert a `queued` job, dispatched now, with the kind's `max_attempts`."""
        statement = (
            insert(JobRecord)
            .values(
                pilot_id=actor.value,
                kind=kind.name,
                max_attempts=kind.max_attempts,
                payload=kind.payload_codec.encode(payload),
            )
            .returning(JobRecord.id)
        )
        job_id = (await self.session.execute(statement)).scalar_one()
        return QueuedJob(JobId(job_id), kind.name)


@dataclass(frozen=True, slots=True)
class PostgresJobRunner:
    """
    The unscoped `JobRunner` on one unit of work's session.

    Leases compare with the database's `now()`, so every worker uses the
    same clock. Each write after the claim is fenced on the claimed attempts.
    """

    session: AsyncSession

    async def claim(
        self, job_id: JobId, kind_name: str, lease: timedelta
    ) -> ClaimedJob[JsonValue] | None:
        """Take the job if it is free, leasing it for `lease`."""
        free = or_(
            JobRecord.status == JobStatus.QUEUED,
            and_(
                JobRecord.status == JobStatus.RUNNING,
                JobRecord.lease_until < func.now(),
            ),
        )
        statement = (
            update(JobRecord)
            .where(
                JobRecord.id == job_id.value,
                JobRecord.kind == kind_name,
                JobRecord.attempts < JobRecord.max_attempts,
                free,
            )
            .values(
                status=JobStatus.RUNNING,
                attempts=JobRecord.attempts + 1,
                lease_until=func.now() + lease,
                updated_at=func.now(),
            )
            .returning(
                JobRecord.pilot_id,
                JobRecord.kind,
                JobRecord.attempts,
                JobRecord.payload,
            )
            .execution_options(**_NO_SYNC)
        )
        row = (await self.session.execute(statement)).one_or_none()
        if row is None:
            return None
        return ClaimedJob(
            id=job_id,
            pilot_id=PilotId(row.pilot_id),
            kind=row.kind,
            attempts=row.attempts,
            payload=cast("JsonValue", row.payload),
        )

    async def renew(self, job_id: JobId, attempts: int, lease: timedelta) -> bool:
        """Extend the lease, fenced on `attempts`."""
        return await self._update_running(
            job_id, attempts, lease_until=func.now() + lease
        )

    async def complete(self, job_id: JobId, attempts: int, result: JsonValue) -> bool:
        """Set `succeeded` and store `result`, fenced on `attempts`."""
        return await self._update_running(
            job_id,
            attempts,
            status=JobStatus.SUCCEEDED,
            result=result,
            lease_until=None,
        )

    async def fail(self, job_id: JobId, attempts: int, error_code: str) -> bool:
        """Record `error_code` and requeue or fail the job, fenced on `attempts`."""
        status = case(
            (JobRecord.attempts >= JobRecord.max_attempts, JobStatus.FAILED.value),
            else_=JobStatus.QUEUED.value,
        )
        return await self._update_running(
            job_id, attempts, status=status, error_code=error_code, lease_until=None
        )

    async def _update_running(
        self, job_id: JobId, attempts: int, **values: ColumnElement[object] | object
    ) -> bool:
        """Apply `values` while the row is `running` with `attempts`."""
        statement = (
            update(JobRecord)
            .where(
                JobRecord.id == job_id.value,
                JobRecord.status == JobStatus.RUNNING,
                JobRecord.attempts == attempts,
            )
            .values(**values, updated_at=func.now())
            .returning(JobRecord.id)
            .execution_options(**_NO_SYNC)
        )
        return (await self.session.execute(statement)).scalar_one_or_none() is not None
