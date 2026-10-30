"""
Test-only job kinds and use cases, which no production code imports.

This module runs inside the release image, with `tests/` mounted, and in the
`dev` stage, so it imports only `tally_aloft` and the runtime packages.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from tally_aloft.application.shared.errors import ApplicationError
from tally_aloft.application.shared.jobs.dispatch import dispatch_safely
from tally_aloft.application.shared.jobs.kinds import JobKind
from tests.support.codecs import JSON

if TYPE_CHECKING:
    from collections.abc import Callable

    from tally_aloft.application.ports.job_queue import JobQueue
    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.jobs.kinds import QueuedJob
    from tally_aloft.application.shared.unit_of_work import SharedUnitOfWork
    from tally_aloft.domain.shared.ids import PilotId

# Short limits keep the tests fast, and fit under the test `idle_timeout`.
TEST_LEASE = timedelta(seconds=2)

# Returns its payload wrapped in a result.
ECHO = JobKind(
    name="test.echo",
    payload_codec=JSON,
    result_codec=JSON,
    lease=TEST_LEASE,
    time_limit=timedelta(seconds=6),
)


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
