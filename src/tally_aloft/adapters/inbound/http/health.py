from http import HTTPStatus

from fastapi import APIRouter, Response

from tally_aloft.adapters.inbound.http.dependencies import UseCasesDep  # noqa: TC001  # FastAPI reads route annotations at runtime.
from tally_aloft.adapters.inbound.http.errors import NOT_READY, error_response

router = APIRouter()


@router.get("/livez", include_in_schema=False)
async def livez() -> Response:
    """
    Report that this process routes and handles HTTP requests.

    It touches no dependency, so it fails only when the process can't serve.
    """
    return Response(status_code=HTTPStatus.OK)


@router.get("/readyz", include_in_schema=False)
async def readyz(use_cases: UseCasesDep) -> Response:
    """
    Report whether every dependency works.

    A failure names only the failed checks, never their errors. This route is
    for diagnostics, and the load balancer must not route it publicly.
    """
    failed = await use_cases.check_readiness()
    if len(failed) == 0:
        return Response(status_code=HTTPStatus.OK)
    return error_response(
        HTTPStatus.SERVICE_UNAVAILABLE,
        NOT_READY,
        "Some dependencies are not ready.",
        {"failed_checks": list(failed)},
    )
