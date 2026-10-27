from http import HTTPStatus

from fastapi import APIRouter, Response

router = APIRouter()


@router.get("/livez", include_in_schema=False)
async def livez() -> Response:
    """
    Report that this process routes and handles HTTP requests.

    It touches no dependency, so it fails only when the process can't serve.
    """
    return Response(status_code=HTTPStatus.OK)
