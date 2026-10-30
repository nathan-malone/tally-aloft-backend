"""
Jobs: the kinds of work recorded in the `jobs` table and run by workers.

The job row is the outbox and the record of truth. A message carries only
the job's ID, under its kind's name, so a task always reads the row.
"""

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from tally_aloft.application.shared.codec import Codec
    from tally_aloft.domain.shared.ids import PilotId

# The error codes a failed attempt records when its body raised no coded
# error. They are stored on the job row, beside the bodies' own codes.
TIMEOUT_CODE = "timeout"
UNHANDLED_ERROR_CODE = "unhandled_error"


@dataclass(frozen=True, slots=True)
class JobId:
    """The identifier of a job row, a UUIDv7 that Postgres generates."""

    value: UUID


class JobStatus(StrEnum):
    """Where a job is in its lifecycle."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True, kw_only=True)
class JobKind[P, R]:
    """
    One kind of job: its stable name, codecs and limits.

    `name` is also the Taskiq task's name, so it must never change while
    jobs of the kind may be stored. `lease` is how long a claim holds the job
    before another worker may take it over; the task renews it every half
    lease. `time_limit` bounds the body's run, and `max_attempts` the claims.
    """

    name: str
    payload_codec: Codec[P]
    result_codec: Codec[R]
    max_attempts: int = 5
    lease: timedelta = timedelta(minutes=5)
    time_limit: timedelta = timedelta(minutes=10)

    def __post_init__(self) -> None:
        """Reject limits a job could never run under."""
        if self.max_attempts < 1:
            msg = f"job kind {self.name} needs max_attempts of at least 1"
            raise ValueError(msg)
        if self.lease <= timedelta(0) or self.time_limit <= timedelta(0):
            msg = f"job kind {self.name} needs a positive lease and time limit"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class QueuedJob:
    """A recorded job, as it is dispatched: its ID under its kind's name."""

    id: JobId
    kind_name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ClaimedJob[P]:
    """
    A job a task has claimed, which its body receives.

    `kind` is the kind's name. `attempts` is the value this claim set: every
    later write to the row, a body's progress included, is fenced on it.
    """

    id: JobId
    pilot_id: PilotId
    # The kind's name, not its `JobKind`.
    kind: str
    attempts: int
    payload: P

    def with_payload[Q](self, payload: Q) -> ClaimedJob[Q]:
        """Return this claim carrying `payload`, such as the decoded one."""
        return ClaimedJob(
            id=self.id,
            pilot_id=self.pilot_id,
            kind=self.kind,
            attempts=self.attempts,
            payload=payload,
        )


# A job's work: an async use case that receives its claimed job.
type JobBody[P, R] = Callable[[ClaimedJob[P]], Awaitable[R]]
