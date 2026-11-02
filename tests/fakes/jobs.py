from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from uuid import uuid7

from tally_aloft.application.shared.jobs.kinds import (
    ATTEMPTS_EXHAUSTED_CODE,
    UNKNOWN_KIND_CODE,
    ClaimedJob,
    FailedJob,
    JobId,
    JobStatus,
    QueuedJob,
)
from tests.fakes.database import FakeJobRow, FakeTable

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.jobs.kinds import JobKind
    from tally_aloft.domain.shared.ids import PilotId


# A unit of work's working copy of the jobs.
type FakeJobTable = FakeTable[JobId, FakeJobRow]


@dataclass(frozen=True, slots=True)
class FakeJobRepository:
    """The pilot-scoped `JobRepository` on a fake unit of work."""

    table: FakeJobTable

    async def record[P, R](
        self, actor: PilotId, kind: JobKind[P, R], payload: P
    ) -> QueuedJob:
        """Insert a `queued` job with the kind's `max_attempts`."""
        row = FakeJobRow(
            id=JobId(uuid7()),
            pilot_id=actor,
            kind=kind.name,
            status=JobStatus.QUEUED,
            attempts=0,
            max_attempts=kind.max_attempts,
            lease_until=None,
            last_dispatched_at=self.table.clock.now(),
            payload=kind.payload_codec.encode(payload),
            result=None,
            error_code=None,
        )
        self.table.put(row.id, row)
        return QueuedJob(row.id, kind.name)


@dataclass(frozen=True, slots=True)
class FakeJobRunner:
    """The unscoped `JobRunner` on a fake unit of work, with the same fencing."""

    table: FakeJobTable

    async def claim(
        self, job_id: JobId, kind_name: str, lease: timedelta
    ) -> ClaimedJob[JsonValue] | None:
        """Take the job if it is free, leasing it for `lease`."""
        row = self.table.rows.get(job_id)
        now = self.table.clock.now()
        if (
            row is None
            or row.kind != kind_name
            or _exhausted(row)
            or not _free(row, now)
        ):
            return None
        claimed = replace(
            row,
            status=JobStatus.RUNNING,
            attempts=row.attempts + 1,
            lease_until=now + lease,
        )
        self.table.put(job_id, claimed)
        return ClaimedJob(
            id=job_id,
            pilot_id=claimed.pilot_id,
            kind=claimed.kind,
            attempts=claimed.attempts,
            payload=claimed.payload,
        )

    async def renew(self, job_id: JobId, attempts: int, lease: timedelta) -> bool:
        """Extend the lease, fenced on `attempts`."""
        row = self._running(job_id, attempts)
        if row is None:
            return False
        self.table.put(job_id, replace(row, lease_until=self.table.clock.now() + lease))
        return True

    async def complete(self, job_id: JobId, attempts: int, result: JsonValue) -> bool:
        """Set `succeeded` and store `result`, fenced on `attempts`."""
        row = self._running(job_id, attempts)
        if row is None:
            return False
        self.table.put(
            job_id,
            replace(
                row,
                status=JobStatus.SUCCEEDED,
                result=result,
                error_code=None,
                lease_until=None,
            ),
        )
        return True

    async def fail(self, job_id: JobId, attempts: int, error_code: str) -> bool:
        """Record `error_code` and requeue or fail the job, fenced on `attempts`."""
        row = self._running(job_id, attempts)
        if row is None:
            return False
        status = (
            JobStatus.FAILED if row.attempts >= row.max_attempts else JobStatus.QUEUED
        )
        self.table.put(
            job_id,
            replace(row, status=status, error_code=error_code, lease_until=None),
        )
        return True

    async def fail_unrunnable(
        self, kind_names: frozenset[str], stale_after: timedelta, limit: int
    ) -> list[FailedJob]:
        """Fail up to `limit` free jobs that can never run."""
        now = self.table.clock.now()
        failed: list[FailedJob] = []
        for row in list(self.table.rows.values()):
            if len(failed) >= limit:
                break
            unknown = row.kind not in kind_names
            stale_unknown = unknown and _stale(row, now, stale_after)
            if not _free(row, now) or not (stale_unknown or _exhausted(row)):
                continue
            code = UNKNOWN_KIND_CODE if unknown else ATTEMPTS_EXHAUSTED_CODE
            self.table.put(
                row.id,
                replace(
                    row, status=JobStatus.FAILED, error_code=code, lease_until=None
                ),
            )
            failed.append(FailedJob(row.id, row.kind, code))
        return failed

    async def take_stale(self, stale_after: timedelta, limit: int) -> list[QueuedJob]:
        """Mark up to `limit` stale jobs dispatched now, oldest dispatch first."""
        now = self.table.clock.now()
        stale = sorted(
            (
                row
                for row in self.table.rows.values()
                if not _exhausted(row)
                and _stale(row, now, stale_after)
                and _free(row, now)
            ),
            key=lambda row: row.last_dispatched_at,
        )[:limit]
        for row in stale:
            self.table.put(row.id, replace(row, last_dispatched_at=now))
        return [QueuedJob(row.id, row.kind) for row in stale]

    def _running(self, job_id: JobId, attempts: int) -> FakeJobRow | None:
        row = self.table.rows.get(job_id)
        if row is None or row.status != JobStatus.RUNNING or row.attempts != attempts:
            return None
        return row


def _exhausted(row: FakeJobRow) -> bool:
    """Return whether `row` has used every attempt its kind allows."""
    return row.attempts >= row.max_attempts


def _stale(row: FakeJobRow, now: datetime, stale_after: timedelta) -> bool:
    """Return whether `row` was last dispatched over `stale_after` ago."""
    return row.last_dispatched_at < now - stale_after


def _free(row: FakeJobRow, now: datetime) -> bool:
    """Return whether a claim may take `row`: queued, or its lease expired."""
    if row.status == JobStatus.QUEUED:
        return True
    return (
        row.status == JobStatus.RUNNING
        and row.lease_until is not None
        and row.lease_until < now
    )
