from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from tally_aloft.adapters.inbound.jobs.dependencies import UseCasesDep  # noqa: TC001  # Taskiq reads task annotations at run time.
from tally_aloft.adapters.inbound.jobs.run_job import run_job
from tally_aloft.application.shared.jobs.kinds import JobId

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tally_aloft.application.registry import UseCases
    from tally_aloft.application.shared.jobs.kinds import JobBody, JobKind

# How much longer than a kind's time limit Taskiq lets the whole task run. It
# covers the claim and the outcome's write at the default settings, each
# waiting up to 30 seconds for a pooled connection, 5 to connect and 30 for
# the statement timeout on its UPDATE, the one statement that can wait on a
# lock. The 10 seconds' slack covers the codecs and cancelling the body and
# the renewal. A higher statement timeout needs a higher backstop.
JOB_BACKSTOP = timedelta(seconds=140)


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskDefinition:
    """
    One Taskiq task, as bootstrap registers it.

    `name` is the task's stable name, which messages carry. `timeout` is
    Taskiq's limit on the whole function. A job task names the kind it runs
    in `kind_name`; other tasks, such as scheduled ones, leave it `None`.
    """

    name: str
    function: Callable[..., Awaitable[object]]
    timeout: timedelta
    kind_name: str | None = None


def job_task[P, R](
    kind: JobKind[P, R],
    body: Callable[[UseCases], JobBody[P, R]],
    *,
    backstop: timedelta = JOB_BACKSTOP,
) -> TaskDefinition:
    """
    Return the task that runs jobs of `kind`, registered under its name.

    `body` picks the kind's body, a use case, from `UseCases`. The wrapper
    enforces the kind's time limit around the body; Taskiq's timeout, the
    time limit plus `backstop`, only stops a wrapper that hangs.
    """

    async def run(job_id: str, use_cases: UseCasesDep) -> None:
        await run_job(use_cases, kind, body(use_cases), JobId(UUID(job_id)))

    return TaskDefinition(
        name=kind.name,
        function=run,
        timeout=kind.time_limit + backstop,
        kind_name=kind.name,
    )
