from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import TYPE_CHECKING

from fastapi import FastAPI
from starlette.middleware import Middleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from tally_aloft.adapters.inbound.http import health
from tally_aloft.adapters.inbound.http.errors import (
    ERROR_STATUSES,
    ErrorBody,
    install_exception_handlers,
)
from tally_aloft.bootstrap.container import Overrides, build_use_cases

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from tally_aloft.bootstrap.settings import Settings

_TITLE = "Tally Aloft API"


def create_app(settings: Settings, overrides: Overrides | None = None) -> FastAPI:
    """
    Build the API app with its routers and middleware.

    It opens no connection and builds no adapter. The lifespan builds the
    `UseCases` registry and puts it on `app.state.use_cases`.
    """
    adapter_overrides = Overrides() if overrides is None else overrides

    @asynccontextmanager
    async def lifespan(api: FastAPI) -> AsyncGenerator[None]:
        api.state.use_cases = build_use_cases(settings, adapter_overrides)
        yield

    app = FastAPI(
        title=_TITLE,
        lifespan=lifespan,
        middleware=_middleware(settings),
        # Documenting every route's 422 with the shared body stops FastAPI
        # from adding its own `HTTPValidationError` schema.
        responses={
            HTTPStatus.UNPROCESSABLE_CONTENT: {
                "model": ErrorBody,
                "description": "Validation Error",
            }
        },
    )
    install_exception_handlers(
        app, {**ERROR_STATUSES, **adapter_overrides.error_statuses}
    )
    app.include_router(health.router)
    return app


def _middleware(settings: Settings) -> list[Middleware]:
    """
    Return the app's user middleware, outermost first.

    The proxy-headers wrapper comes first. It never responds, and it must set
    the real client address before anything else reads it. Starlette always
    wraps every user middleware in its own `ServerErrorMiddleware`, which
    answers unhandled errors with a 500 but never reads the client address.
    Uvicorn's own proxy handling is off (`--no-proxy-headers`), so the trusted
    networks come only from settings.
    """
    return [
        Middleware(
            ProxyHeadersMiddleware,
            trusted_hosts=[str(network) for network in settings.forwarded_allow_ips],
        ),
    ]
