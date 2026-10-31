import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tally_aloft.application.shared.jobs.kinds import JobId


@dataclass(slots=True)
class FakeJobQueue:
    """A job queue that records each dispatch, or fails, or never returns."""

    outcome: Literal["ok", "fail", "hang"] = "ok"
    dispatched: list[tuple[JobId, str]] = field(default_factory=list)

    async def dispatch(self, job_id: JobId, kind_name: str) -> None:
        """Act out the configured outcome, recording the dispatch when it works."""
        match self.outcome:
            case "ok":
                self.dispatched.append((job_id, kind_name))
            case "fail":
                msg = "the queue refused the job"
                raise ConnectionError(msg)
            case "hang":
                await asyncio.Event().wait()
