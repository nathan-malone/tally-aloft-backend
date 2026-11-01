from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import TYPE_CHECKING

from fastapi import FastAPI
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from tally_aloft.adapters.inbound.http import health
from tally_aloft.adapters.inbound.http.errors import (
    ERROR_STATUSES,
    ErrorBody,
    UnhandledErrorMiddleware,
    install_exception_handlers,
)
from tally_aloft.adapters.inbound.http.operation_ids import operation_id
from tally_aloft.bootstrap.container import (
    TASKS,
    Overrides,
    build_use_cases,
    job_kind_names,
)
from tally_aloft.bootstrap.resources import open_resources

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from tally_aloft.bootstrap.settings import Settings

_TITLE = "Tally Aloft API"
# What browsers may send cross-origin: the client contract's methods and
# request headers. Starlette also allows the CORS-safelisted headers.
_CORS_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
_CORS_HEADERS = (
    "Authorization",
    "Content-Type",
    "Idempotency-Key",
    "X-Client-Platform",
    "X-Client-Version",
    "traceparent",
    "tracestate",
    "sentry-trace",
    "baggage",
)


def create_app(settings: Settings, overrides: Overrides | None = None) -> FastAPI:
    """
    Build the API app with its routers and middleware.

    It opens no connection and builds no adapter. The lifespan opens the shared
    resources, builds the `UseCases` registry on them, puts it on
    `app.state.use_cases`, and closes the resources on shutdown.
    """
    resolved_overrides = Overrides() if overrides is None else overrides

    @asynccontextmanager
    async def lifespan(api: FastAPI) -> AsyncGenerator[None]:
        async with open_resources(settings) as resources:
            api.state.use_cases = build_use_cases(
                settings, resources, resolved_overrides, job_kind_names(TASKS)
            )
            yield

    app = FastAPI(
        title=_TITLE,
        lifespan=lifespan,
        middleware=_middleware(settings),
        generate_unique_id_function=operation_id,
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
        app, {**ERROR_STATUSES, **resolved_overrides.error_statuses}
    )
    app.include_router(health.router)
    return app


def _middleware(settings: Settings) -> list[Middleware]:
    """
    Return the app's user middleware, outermost first.

    1. The proxy-headers wrapper comes first. It never responds, and it must
       set the real client address before anything else reads it. Uvicorn's
       own proxy handling is off (`--no-proxy-headers`), so the trusted
       networks come only from settings.
    2. CORS is the outermost middleware that responds, so its headers reach
       every response to an allowed origin, rejections included. Credentials
       stay off, because clients authenticate with bearer tokens.
    3. The unhandled-error middleware sits inside CORS, so its `500`s carry
       CORS headers. Starlette's own `ServerErrorMiddleware` wraps this whole
       list, and its responses have none.

    Later middleware, such as the client-version check, goes after these.
    """
    return [
        Middleware(
            ProxyHeadersMiddleware,
            trusted_hosts=[str(network) for network in settings.forwarded_allow_ips],
        ),
        Middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=_CORS_METHODS,
            allow_headers=_CORS_HEADERS,
            allow_credentials=False,
        ),
        Middleware(UnhandledErrorMiddleware),
    ]
