from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from sqlalchemy import and_, case, func, insert, or_, select, update

from tally_aloft.adapters.outbound.postgres.tables.jobs import JobRecord
from tally_aloft.application.shared.jobs.kinds import (
    ATTEMPTS_EXHAUSTED_CODE,
    UNKNOWN_KIND_CODE,
    ClaimedJob,
    FailedJob,
    JobId,
    JobStatus,
    QueuedJob,
)
from tally_aloft.domain.shared.ids import PilotId

if TYPE_CHECKING:
    from datetime import timedelta
    from uuid import UUID

    from sqlalchemy import CTE, ColumnElement, Select
    from sqlalchemy.ext.asyncio import AsyncSession

    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.jobs.kinds import JobKind

# ORM-enabled updates would otherwise look for the rows in the session.
_NO_SYNC = {"synchronize_session": False}


def _lease_expired() -> ColumnElement[bool]:
    """Return whether a row's lease has run out, on the database's clock."""
    return JobRecord.lease_until < func.now()


def _free() -> ColumnElement[bool]:
    """Return whether a claim may take a row: `queued`, or its lease expired."""
    return or_(
        JobRecord.status == JobStatus.QUEUED,
        and_(JobRecord.status == JobStatus.RUNNING, _lease_expired()),
    )


def _exhausted() -> ColumnElement[bool]:
    """Return whether a row has used every attempt its kind allows."""
    return JobRecord.attempts >= JobRecord.max_attempts


def _stale(stale_after: timedelta) -> ColumnElement[bool]:
    """Return whether a row was last dispatched over `stale_after` ago."""
    return JobRecord.last_dispatched_at < func.now() - stale_after


def _locked_ids(candidates: Select[UUID], limit: int) -> CTE:
    """
    Return the IDs of up to `limit` of `candidates`, locked, skipping locked rows.

    `MATERIALIZED` makes Postgres run the locking select once, so the update
    joined to it can't touch more than `limit` rows.
    """
    return (
        candidates.limit(limit)
        .with_for_update(skip_locked=True)
        .cte("locked")
        .prefix_with("MATERIALIZED")
    )


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

    Leases and staleness compare with the database's `now()`, so every worker
    and sweep uses the same clock. A task's writes after its claim are fenced
    on the claimed attempts; the sweeper's two writes aren't, and lock the
    rows they take with `SKIP LOCKED` instead.
    """

    session: AsyncSession

    async def claim(
        self, job_id: JobId, kind_name: str, lease: timedelta
    ) -> ClaimedJob[JsonValue] | None:
        """Take the job if it is free, leasing it for `lease`."""
        statement = (
            update(JobRecord)
            .where(
                JobRecord.id == job_id.value,
                JobRecord.kind == kind_name,
                ~_exhausted(),
                _free(),
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
        """Set `succeeded`, store `result`, clear an earlier attempt's code."""
        return await self._update_running(
            job_id,
            attempts,
            status=JobStatus.SUCCEEDED,
            result=result,
            error_code=None,
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

    async def fail_unrunnable(
        self, kind_names: frozenset[str], stale_after: timedelta, limit: int
    ) -> list[FailedJob]:
        """Fail up to `limit` free jobs that can never run, skipping locked rows."""
        unknown = JobRecord.kind.not_in(sorted(kind_names))
        never = or_(_exhausted(), and_(unknown, _stale(stale_after)))
        # Grouped by status, so each branch can use that status's partial index.
        unrunnable = or_(
            and_(JobRecord.status == JobStatus.QUEUED, never),
            and_(JobRecord.status == JobStatus.RUNNING, _lease_expired(), never),
        )
        locked = _locked_ids(select(JobRecord.id).where(unrunnable), limit)
        statement = (
            update(JobRecord)
            .where(JobRecord.id == locked.c.id)
            .values(
                status=JobStatus.FAILED,
                error_code=case(
                    (unknown, UNKNOWN_KIND_CODE), else_=ATTEMPTS_EXHAUSTED_CODE
                ),
                lease_until=None,
                updated_at=func.now(),
            )
            .returning(JobRecord.id, JobRecord.kind, JobRecord.error_code)
            .execution_options(**_NO_SYNC)
        )
        rows = (await self.session.execute(statement)).all()
        return [FailedJob(JobId(row.id), row.kind, row.error_code) for row in rows]

    async def take_stale(self, stale_after: timedelta, limit: int) -> list[QueuedJob]:
        """Mark up to `limit` stale jobs dispatched now, skipping locked rows."""
        stale = (
            select(JobRecord.id)
            .where(
                ~_exhausted(),
                _stale(stale_after),
                _free(),
            )
            .order_by(JobRecord.last_dispatched_at)
        )
        locked = _locked_ids(stale, limit)
        statement = (
            update(JobRecord)
            .where(JobRecord.id == locked.c.id)
            .values(last_dispatched_at=func.now(), updated_at=func.now())
            .returning(JobRecord.id, JobRecord.kind)
            .execution_options(**_NO_SYNC)
        )
        rows = (await self.session.execute(statement)).all()
        return [QueuedJob(JobId(row.id), row.kind) for row in rows]

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
