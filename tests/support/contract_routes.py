"""
Test-only routes and errors that exercise the HTTP contract machinery.

The `/test` prefix and the `contract` tag keep these routes and their operation
IDs clear of any real router. The image never mounts this module.
"""

from http import HTTPStatus

from fastapi import APIRouter
from pydantic import BaseModel

from tally_aloft.adapters.inbound.http.errors import error_responses
from tally_aloft.application.shared.errors import ApplicationError

router = APIRouter(prefix="/test", tags=["contract"])


class ContractMappedError(ApplicationError):
    """A test error that the tests map to a status through `Overrides`."""

    code = "contract_mapped"


class ContractChildError(ContractMappedError):
    """A subclass of a mapped error, with no mapping of its own."""

    code = "contract_child"


class ContractUnmappedError(ApplicationError):
    """A test error that no table maps."""

    code = "contract_unmapped"


MAPPED_STATUS = HTTPStatus.NOT_FOUND
MAPPED_MESSAGE = "The contract resource doesn't exist."
LEAK_MARKER = "secret-marker"


class Payload(BaseModel):
    """A request body with one required field."""

    count: int


@router.get("/query")
async def query(count: int) -> int:
    """Echo a required integer query parameter."""
    return count


@router.post("/body")
async def body(payload: Payload) -> int:
    """Echo a JSON body's field."""
    return payload.count


@router.get("/mapped", responses=error_responses(MAPPED_STATUS))
async def mapped() -> None:
    """Raise the error the tests map."""
    raise ContractMappedError(MAPPED_MESSAGE)


@router.get("/unmapped")
async def unmapped() -> None:
    """Raise an error no table maps."""
    msg = "Nothing maps this error."
    raise ContractUnmappedError(msg)


@router.get("/crash")
async def crash() -> None:
    """Raise an exception the app doesn't handle, with a marker in its text."""
    raise RuntimeError(LEAK_MARKER)
