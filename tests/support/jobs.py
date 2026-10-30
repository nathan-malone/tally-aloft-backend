"""
Test-only job kinds and use cases, which no production code imports.

This module runs inside the release image, with `tests/` mounted, and in the
`dev` stage, so it imports only `tally_aloft` and the runtime packages.
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from tally_aloft.adapters.inbound.jobs.tasks import job_task
from tally_aloft.application.shared.errors import ApplicationError
from tally_aloft.application.shared.jobs.dispatch import dispatch_safely
from tally_aloft.application.shared.jobs.kinds import JobKind
from tests.support.codecs import JSON

if TYPE_CHECKING:
    from collections.abc import Callable

    from tally_aloft.application.ports.job_queue import JobQueue
    from tally_aloft.application.registry import UseCases
    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.jobs.kinds import ClaimedJob, QueuedJob
    from tally_aloft.application.shared.unit_of_work import SharedUnitOfWork
    from tally_aloft.domain.shared.ids import PilotId

# Short limits keep the tests fast. Each kind's time limit plus the backstop
# stays under the test worker's 10-second idle_timeout: at most 6 + 3 seconds.
TEST_LEASE = timedelta(seconds=2)
TEST_TIME_LIMIT = timedelta(seconds=6)
# Room for the claim and the outcome's write under a loaded test run.
TEST_BACKSTOP = timedelta(seconds=3)

# Returns its payload wrapped in a result.
ECHO = JobKind(
    name="test.echo",
    payload_codec=JSON,
    result_codec=JSON,
    lease=TEST_LEASE,
    time_limit=TEST_TIME_LIMIT,
)
# Raises `BodyFailedError` for the payload "coded", any other payload a
# RuntimeError.
FAIL = JobKind(
    name="test.fail",
    payload_codec=JSON,
    result_codec=JSON,
    max_attempts=2,
    lease=TEST_LEASE,
    time_limit=TEST_TIME_LIMIT,
)
# Sleeps for its payload's seconds on its first attempt only.
SLOW_FIRST_ATTEMPT = JobKind(
    name="test.slow_first_attempt",
    payload_codec=JSON,
    result_codec=JSON,
    lease=TEST_LEASE,
    time_limit=TEST_TIME_LIMIT,
)
# Runs past its one-second time limit.
OVERRUN = JobKind(
    name="test.overrun",
    payload_codec=JSON,
    result_codec=JSON,
    lease=TEST_LEASE,
    time_limit=timedelta(seconds=1),
)


class BodyFailedError(ApplicationError):
    """The coded error `test.fail` raises."""

    code = "test_failure"


async def echo(job: ClaimedJob[JsonValue]) -> JsonValue:
    """Return the payload, wrapped."""
    return {"echo": job.payload}


async def fail(job: ClaimedJob[JsonValue]) -> JsonValue:
    """Raise a coded error or an unhandled one, as the payload says."""
    if job.payload == "coded":
        msg = "The test job failed."
        raise BodyFailedError(msg)
    msg = "the test job broke"
    raise RuntimeError(msg)


async def slow_first_attempt(job: ClaimedJob[JsonValue]) -> JsonValue:
    """Sleep for the payload's seconds on the first attempt, then return."""
    if job.attempts == 1 and isinstance(job.payload, int | float):
        await asyncio.sleep(job.payload)
    return job.attempts


async def overrun(job: ClaimedJob[JsonValue]) -> JsonValue:
    """Sleep far past any time limit."""
    await asyncio.sleep(60)
    return job.attempts


class RecordingFailedError(ApplicationError):
    """The test use case failed after recording its job, as asked."""

    code = "test_recording_failed"


@dataclass(frozen=True, slots=True)
class RecordTestJob:
    """
    A use case that records a job, then dispatches it after its commit.

    It is the outbox pattern every job-producing use case follows. With
    `fail_after_record`, it raises inside its unit of work instead.
    """

    uow: Callable[[], SharedUnitOfWork]
    queue: JobQueue

    async def __call__(
        self,
        actor: PilotId,
        payload: JsonValue,
        *,
        kind: JobKind[JsonValue, JsonValue] = ECHO,
        fail_after_record: bool = False,
    ) -> QueuedJob:
        """Record a job of `kind` for `actor`, then dispatch it."""
        async with self.uow() as uow:
            job = await uow.jobs.record(actor, kind, payload)
            if fail_after_record:
                msg = "The test use case failed."
                raise RecordingFailedError(msg)
        await dispatch_safely(self.queue, [job])
        return job


def _always[T](body: T) -> Callable[[UseCases], T]:
    """Return a body picker for a test body, which isn't on `UseCases`."""
    return lambda _use_cases: body


TEST_JOB_TASKS = (
    job_task(ECHO, _always(echo), backstop=TEST_BACKSTOP),
    job_task(FAIL, _always(fail), backstop=TEST_BACKSTOP),
    job_task(SLOW_FIRST_ATTEMPT, _always(slow_first_attempt), backstop=TEST_BACKSTOP),
    job_task(OVERRUN, _always(overrun), backstop=TEST_BACKSTOP),
)
