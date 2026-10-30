from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from tally_aloft.application.ports.clock import Clock
    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.jobs.kinds import JobId
    from tally_aloft.domain.shared.ids import PilotId


@dataclass(frozen=True, slots=True, kw_only=True)
class FakeJobRow:
    """One row of the fake `jobs` table, as Postgres stores it."""

    id: JobId
    pilot_id: PilotId
    kind: str
    status: str
    attempts: int
    max_attempts: int
    lease_until: datetime | None
    payload: JsonValue
    result: JsonValue
    error_code: str | None


@dataclass(slots=True)
class FakeDatabase:
    """
    Every fake table, which fake units of work read and commit to.

    `clock` stands in for the database's `now()`. A new fake table joins this
    class, and `FakeUnitOfWork` copies and writes it back as it does `jobs`.
    """

    clock: Clock
    jobs: dict[JobId, FakeJobRow] = field(default_factory=dict)
