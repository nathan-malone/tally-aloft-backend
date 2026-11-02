"""
The shared error body, and how errors become HTTP responses.

Every error a client can read is `{code, message, details}`. Use-case errors map
to statuses by exact class in `ERROR_STATUSES`; anything unmapped or unhandled
becomes `500 internal_error`.
"""

import logging
from http import HTTPStatus
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.utils import is_body_allowed_for_status_code
from pydantic import BaseModel, JsonValue
from starlette.exceptions import HTTPException

from tally_aloft.application.shared.errors import IdempotencyKeyReusedError
from tally_aloft.domain.shared.errors import CodedError, is_concrete

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from fastapi import FastAPI, Request
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

_logger = logging.getLogger(__name__)

# The codes of errors from the framework rather than a use case. Each is part
# of the client contract (client-contract.md, "Errors").
VALIDATION_FAILED = "validation_failed"
HTTP_ERROR_FALLBACK = "http_error"
INTERNAL_ERROR = "internal_error"
NOT_READY = "not_ready"

_INTERNAL_MESSAGE = "An internal error occurred."
_VALIDATION_MESSAGE = "The request is invalid."
_HTTP_ERROR_MESSAGE = "The request failed."

# Codes for the 4xx errors the framework itself raises, written out rather
# than taken from the status phrase, which Python's releases reword. Any other
# 4xx is `HTTP_ERROR_FALLBACK`.
HTTP_ERROR_CODES: Final[Mapping[int, str]] = MappingProxyType(
    {
        HTTPStatus.NOT_FOUND: "not_found",
        HTTPStatus.METHOD_NOT_ALLOWED: "method_not_allowed",
    }
)

# The status of each concrete domain and application error, keyed by its exact
# class: a subclass needs its own entry. A test fails when one is missing.
ERROR_STATUSES: Final[Mapping[type[CodedError], HTTPStatus]] = MappingProxyType(
    {IdempotencyKeyReusedError: HTTPStatus.UNPROCESSABLE_CONTENT}
)


class ErrorBody(BaseModel):
    """
    The body of every error response.

    `details` is an object whose keys depend on `code`. Every field is required,
    so the schema promises clients all three.
    """

    code: str
    message: str
    details: dict[str, JsonValue]


def error_response(
    status: int,
    code: str,
    message: str,
    details: dict[str, JsonValue] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Return an error response with the shared body."""
    body = ErrorBody(
        code=code, message=message, details={} if details is None else details
    )
    return JSONResponse(
        body.model_dump(mode="json"),
        status_code=status,
        headers=None if headers is None else dict(headers),
    )


def error_responses(*statuses: int) -> dict[int | str, dict[str, object]]:
    """Return a router's `responses` entries documenting `statuses` with `ErrorBody`."""
    return {status: {"model": ErrorBody} for status in statuses}


def unmapped_errors(
    classes: Iterable[type[CodedError]],
    table: Mapping[type[CodedError], HTTPStatus],
) -> list[type[CodedError]]:
    """Return each concrete class in `classes` that has no entry in `table`."""
    return [
        error_class
        for error_class in classes
        if is_concrete(error_class) and error_class not in table
    ]


def install_exception_handlers(
    app: FastAPI, statuses: Mapping[type[CodedError], HTTPStatus]
) -> None:
    """
    Answer handled errors with the shared body, mapping use cases by `statuses`.

    Each handler takes any `Exception`, the type Starlette's handlers have, and
    narrows it with `isinstance`; the branch that re-raises is unreachable,
    because Starlette picks the handler by the exception's class. Only the
    use-case handler needs `statuses`, so only it is defined here.
    """

    async def coded_error(_request: Request, error: Exception) -> Response:
        if not isinstance(error, CodedError):
            raise error
        status = statuses.get(type(error))
        if status is None:
            # Unmapped: the unhandled-error middleware answers and logs it.
            raise error
        return error_response(status, error.code, error.message)

    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(CodedError, coded_error)


async def _validation_error(_request: Request, error: Exception) -> Response:
    """Answer a request that fails validation, without echoing its input."""
    if not isinstance(error, RequestValidationError):
        raise error
    errors: list[JsonValue] = [
        {
            # List indices stay integers, so they differ from object keys.
            "loc": [
                part if isinstance(part, int) else str(part) for part in detail["loc"]
            ],
            "message": str(detail["msg"]),
            "type": str(detail["type"]),
        }
        for detail in error.errors()
    ]
    return error_response(
        HTTPStatus.UNPROCESSABLE_CONTENT,
        VALIDATION_FAILED,
        _VALIDATION_MESSAGE,
        {"errors": errors},
    )


async def _http_error(request: Request, error: Exception) -> Response:
    """Answer an `HTTPException` the framework or a dependency raised."""
    if not isinstance(error, HTTPException):
        raise error
    status = error.status_code
    if not is_body_allowed_for_status_code(status):
        return Response(status_code=status, headers=error.headers)
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        log_unhandled(error, request.method, request.url.path)
        return error_response(
            HTTPStatus.INTERNAL_SERVER_ERROR, INTERNAL_ERROR, _INTERNAL_MESSAGE
        )
    message = error.detail if isinstance(error.detail, str) else _HTTP_ERROR_MESSAGE
    return error_response(
        status,
        HTTP_ERROR_CODES.get(status, HTTP_ERROR_FALLBACK),
        message,
        headers=error.headers,
    )


class UnhandledErrorMiddleware:
    """
    Answer any exception nothing else handled with `500 internal_error`.

    It sits just inside `CORSMiddleware`, so the `500` carries CORS headers:
    Starlette's own `ServerErrorMiddleware` sits outside every user middleware,
    and its responses have none. The body never holds the exception's text;
    the exception is logged with its type and stack trace instead. This is the
    only place an unhandled exception is visible, because no middleware outside
    it sees one, so exception telemetry is recorded here.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap `app`."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the app, answering an unhandled exception if nothing was sent."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as error:
            if started:
                # Too late for a new response; the server logs and closes it.
                raise
            log_unhandled(error, str(scope["method"]), str(scope["path"]))
            response = error_response(
                HTTPStatus.INTERNAL_SERVER_ERROR, INTERNAL_ERROR, _INTERNAL_MESSAGE
            )
            await response(scope, receive, send)


def log_unhandled(error: BaseException, method: str, path: str) -> None:
    """Log an error that became a `500`, with its type and stack trace."""
    _logger.error(
        "Unhandled %s on %s %s",
        type(error).__qualname__,
        method,
        path,
        exc_info=error,
    )
