from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from sqlalchemy import and_, delete, null, select, update
from sqlalchemy.dialects.postgresql import insert

from tally_aloft.adapters.outbound.postgres.statements import NO_SYNC, locked_ids
from tally_aloft.adapters.outbound.postgres.tables.idempotency_keys import (
    IdempotencyKeyRecord,
)
from tally_aloft.application.shared.idempotency import StoredKey

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession

    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.idempotency import IdempotencyRequest


def _is_key(request: IdempotencyRequest) -> ColumnElement[bool]:
    """Return whether a row is the request's key: its actor, operation and key."""
    return and_(
        IdempotencyKeyRecord.pilot_id == request.actor.value,
        IdempotencyKeyRecord.operation == request.operation,
        IdempotencyKeyRecord.key == request.key,
    )


@dataclass(frozen=True, slots=True)
class PostgresIdempotencyRepository:
    """The pilot-scoped `IdempotencyRepository` on one unit of work's session."""

    session: AsyncSession

    async def reserve(self, request: IdempotencyRequest) -> bool:
        """Insert the key row unless it exists, waiting on an uncommitted one."""
        statement = (
            insert(IdempotencyKeyRecord)
            .values(
                pilot_id=request.actor.value,
                operation=request.operation,
                key=request.key,
                request_hash=request.request_hash,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    IdempotencyKeyRecord.pilot_id,
                    IdempotencyKeyRecord.operation,
                    IdempotencyKeyRecord.key,
                ]
            )
            .returning(IdempotencyKeyRecord.id)
        )
        inserted = (await self.session.execute(statement)).scalar_one_or_none()
        return inserted is not None

    async def find(self, request: IdempotencyRequest) -> StoredKey | None:
        """Return the key row, telling a stored JSON `null` from no result."""
        statement = select(
            IdempotencyKeyRecord.request_hash,
            IdempotencyKeyRecord.result,
            # SQL NULL, not JSON `null`, which reads back as None too.
            IdempotencyKeyRecord.result.is_not(null()).label("has_result"),
        ).where(_is_key(request))
        row = (await self.session.execute(statement)).one_or_none()
        if row is None:
            return None
        return StoredKey(
            request_hash=row.request_hash,
            result=cast("JsonValue", row.result),
            has_result=row.has_result,
        )

    async def store_result(
        self, request: IdempotencyRequest, result: JsonValue
    ) -> None:
        """Store `result` on the key row, a JSON `null` as JSON."""
        statement = (
            update(IdempotencyKeyRecord)
            .where(_is_key(request))
            .values(result=result)
            .execution_options(**NO_SYNC)
        )
        _ = await self.session.execute(statement)


@dataclass(frozen=True, slots=True)
class PostgresIdempotencyMaintenance:
    """The unscoped `IdempotencyMaintenance` on one unit of work's session."""

    session: AsyncSession

    async def delete_created_before(self, cutoff: datetime, limit: int) -> int:
        """Delete up to `limit` keys created before `cutoff`, skipping locked rows."""
        old = select(IdempotencyKeyRecord.id).where(
            IdempotencyKeyRecord.created_at < cutoff
        )
        locked = locked_ids(old, limit)
        statement = (
            delete(IdempotencyKeyRecord)
            .where(IdempotencyKeyRecord.id == locked.c.id)
            .returning(IdempotencyKeyRecord.id)
            .execution_options(**NO_SYNC)
        )
        return len((await self.session.execute(statement)).all())
