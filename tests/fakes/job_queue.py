import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tally_aloft.application.shared.jobs.kinds import JobId


@dataclass(slots=True)
class FakeJobQueue:
    """
    A job queue that records each dispatch, or fails, or never returns.

    A dispatch of a job in `failing` raises `ConnectionError`; every other
    dispatch acts out `outcome`.
    """

    outcome: Literal["ok", "fail", "hang"] = "ok"
    dispatched: list[tuple[JobId, str]] = field(default_factory=list)
    failing: set[JobId] = field(default_factory=set)

    async def dispatch(self, job_id: JobId, kind_name: str) -> None:
        """Act out the configured outcome, recording the dispatch when it works."""
        if job_id in self.failing:
            msg = "the queue refused the job"
            raise ConnectionError(msg)
        match self.outcome:
            case "ok":
                self.dispatched.append((job_id, kind_name))
            case "fail":
                msg = "the queue refused the job"
                raise ConnectionError(msg)
            case "hang":
                await asyncio.Event().wait()
