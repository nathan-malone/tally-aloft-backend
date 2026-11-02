"""
A test-only write route over `IdempotentWrite`, on the contract app.

The key and the request hash travel in the body, because the real
`Idempotency-Key` header and request hash come with write endpoints. The
actor is fixed by the factory, because authentication doesn't exist yet. The
route uses the production error table, so it proves the reuse error's `422`.
"""

from http import HTTPStatus
from typing import TYPE_CHECKING

from fastapi import APIRouter
from pydantic import BaseModel

from tests.support.contract_routes import contract_app
from tests.support.idempotency import WriteCommand

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tally_aloft.domain.shared.ids import PilotId
    from tests.support.idempotency import IdempotentWrite


class WriteBody(BaseModel):
    """A write request: its key, its hash, and what it writes."""

    key: str
    request_hash: str
    note: str


class ReceiptBody(BaseModel):
    """A write's response."""

    job_id: str
    note: str


def write_app(use_case: IdempotentWrite, actor: PilotId) -> FastAPI:
    """Return the contract app with `POST /test/writes`, writing as `actor`."""
    router = APIRouter(prefix="/test", tags=["contract"])

    # The router chooses the status from the use case's result.
    @router.post("/writes", status_code=HTTPStatus.CREATED)
    async def write(body: WriteBody) -> ReceiptBody:
        receipt = await use_case(
            WriteCommand(
                actor=actor,
                key=body.key,
                request_hash=body.request_hash,
                note=body.note,
            )
        )
        return ReceiptBody(job_id=str(receipt.job_id.value), note=receipt.note)

    app = contract_app()
    app.include_router(router)
    return app
