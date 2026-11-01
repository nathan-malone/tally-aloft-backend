"""
A test-only idempotent write, its command, result and codec.

No write endpoint exists yet, so this use case drives `run_once`. Its write
records an `ECHO` job, which both stores can count, and the job's ID is new
on every run, so equal receipts prove a replay. No test runs the job, so it
isn't dispatched.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from tally_aloft.application.shared.idempotency import IdempotencyRequest, run_once
from tally_aloft.application.shared.jobs.kinds import JobId
from tests.support.jobs import ECHO

if TYPE_CHECKING:
    from collections.abc import Callable

    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.unit_of_work import SharedUnitOfWork
    from tally_aloft.domain.shared.ids import PilotId

OPERATION = "test.idempotent_write"


@dataclass(frozen=True, slots=True, kw_only=True)
class WriteCommand:
    """A write's command: what a write router would build from a request."""

    actor: PilotId
    key: str
    request_hash: str
    note: str
    # Raise after the write, inside the unit of work.
    fail: bool = False


@dataclass(frozen=True, slots=True)
class WriteReceipt:
    """What the write returns: the job it recorded, and its note."""

    job_id: JobId
    note: str


class ReceiptCodec:
    """Stores a receipt as `{"job_id": "<uuid>", "note": "<note>"}`."""

    def encode(self, value: WriteReceipt) -> JsonValue:
        """Return the receipt as JSON."""
        return {"job_id": str(value.job_id.value), "note": value.note}

    def decode(self, data: JsonValue) -> WriteReceipt:
        """Return the receipt `encode` stored, or raise `ValueError`."""
        match data:
            case {"job_id": str() as job_id, "note": str() as note}:
                return WriteReceipt(JobId(UUID(job_id)), note)
            case _:
                msg = "a stored receipt has a string job_id and note"
                raise ValueError(msg)


RECEIPT_CODEC = ReceiptCodec()


async def record_receipt(
    uow: SharedUnitOfWork, actor: PilotId, note: str
) -> WriteReceipt:
    """Write once: record an `ECHO` job for `actor`, and return its receipt."""
    job = await uow.jobs.record(actor, ECHO, note)
    return WriteReceipt(job.id, note)


@dataclass(frozen=True, slots=True)
class IdempotentWrite:
    """A use case whose write runs once per key, as every write use case's does."""

    uow: Callable[[], SharedUnitOfWork]

    async def __call__(self, command: WriteCommand) -> WriteReceipt:
        """Record the write's job, or return the receipt a first call stored."""
        request = IdempotencyRequest(
            command.actor, OPERATION, command.key, command.request_hash
        )
        async with self.uow() as uow:

            async def action() -> WriteReceipt:
                receipt = await record_receipt(uow, command.actor, command.note)
                if command.fail:
                    msg = "the test write failed"
                    raise RuntimeError(msg)
                return receipt

            return await run_once(uow, request, action, RECEIPT_CODEC)
