"""
Idempotency: a retried or concurrent write with the same key runs once.

A write use case wraps its change in `run_once`, inside its unit of work. The
key row on (actor, operation, key) is inserted first, so a concurrent repeat
waits on it, and it stores the use case's encoded result, never an HTTP
status and body.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from tally_aloft.application.shared.unit_of_work import SharedUnitOfWork

if TYPE_CHECKING:
    from datetime import datetime

    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.domain.shared.ids import PilotId


@dataclass(frozen=True, slots=True)
class IdempotencyRequest:
    """
    The identity of one idempotent write, and a hash of what it asks for.

    `actor` scopes the key, so one pilot can't replay another's key and read
    their result. `operation` is a constant in the use case's own module.
    `request_hash` covers the request's path parameters and body.
    """

    actor: PilotId
    operation: str
    key: str
    request_hash: str


@dataclass(frozen=True, slots=True)
class StoredKey:
    """
    A key row as a repeat reads it.

    `has_result` is false while the row has no result yet, which only the
    first call's own transaction should ever see.
    """

    request_hash: str
    result: JsonValue
    has_result: bool


class IdempotencyRepository(Protocol):
    """
    A pilot's idempotency keys. Every use case can reach it as `uow.idempotency`.

    Every method takes the request, so the pilot always comes from its actor.
    """

    async def reserve(self, request: IdempotencyRequest) -> bool:
        """
        Insert the key row, and return whether this call inserted it.

        A row that already exists makes it return False. Under READ COMMITTED,
        an insert of a key another transaction has inserted but not committed
        waits for that transaction to end.
        """
        ...

    async def find(self, request: IdempotencyRequest) -> StoredKey | None:
        """Return the key row for the request's actor, operation and key."""
        ...

    async def store_result(
        self, request: IdempotencyRequest, result: JsonValue
    ) -> None:
        """Store `result` on the key row this transaction reserved."""
        ...


class IdempotencyMaintenance(Protocol):
    """
    Every pilot's idempotency keys: the cleanup's unscoped operations.

    Only the cleanup use case reaches it, through
    `IdempotencyMaintenanceUnitOfWork`.
    """

    async def delete_created_before(self, cutoff: datetime, limit: int) -> int:
        """
        Delete up to `limit` keys created before `cutoff`, and return the count.

        It skips rows another transaction has locked.
        """
        ...


class IdempotencyMaintenanceUnitOfWork(SharedUnitOfWork, Protocol):
    """
    A unit of work that also exposes the unscoped `idempotency_maintenance`.

    Domain units of work extend `SharedUnitOfWork`, not this, so feature use
    cases can't reach another pilot's keys by accident.
    """

    @property
    def idempotency_maintenance(self) -> IdempotencyMaintenance:
        """Return the idempotency maintenance repository on this session."""
        ...
