import asyncio
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class FakeDependencyCheck:
    """A readiness check that passes, fails with `error_text`, or never returns."""

    name: str
    outcome: Literal["pass", "fail", "hang"]
    error_text: str = "boom"

    async def check(self) -> None:
        """Act out the configured outcome."""
        match self.outcome:
            case "pass":
                return
            case "fail":
                raise RuntimeError(self.error_text)
            case "hang":
                await asyncio.Event().wait()
