"""
The client-version check: an "update required" answer for outdated app builds.

It is an upgrade prompt, not a security control (client-contract.md, "Client
versions"), so anything it can't judge passes: a request without both headers,
a platform without a minimum, or a version that doesn't parse.
"""

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING

from starlette.datastructures import Headers

from tally_aloft.adapters.inbound.http.errors import (
    CLIENT_VERSION_UNSUPPORTED,
    error_response,
)
from tally_aloft.adapters.inbound.http.health import HEALTH_PATHS
from tally_aloft.adapters.inbound.http.version_policy import ClientVersion

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from tally_aloft.adapters.inbound.http.version_policy import ClientVersionPolicy

_logger = logging.getLogger(__name__)

PLATFORM_HEADER = "X-Client-Platform"
VERSION_HEADER = "X-Client-Version"
# Vendors call webhooks, and send no app headers.
WEBHOOKS_PREFIX = "/webhooks"
# The most of an unparseable version that is logged.
LOGGED_VERSION_LENGTH = 64
_MESSAGE = "This app version is no longer supported. Update the app to continue."


class ClientVersionMiddleware:
    """
    Answer a request from an app below its platform's minimum with `400`.

    It is plain ASGI, and returns its own response, because an exception raised
    in middleware never reaches FastAPI's exception handlers. It sits inside
    `UnhandledErrorMiddleware`, and so inside CORS, so the `400` carries CORS
    headers. The health and webhook routes are exempt.
    """

    def __init__(self, app: ASGIApp, policy: ClientVersionPolicy) -> None:
        """Wrap `app`, enforcing `policy`."""
        self.app = app
        self.policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Reject an outdated app's HTTP request, and pass everything else."""
        if scope["type"] == "http" and not _is_exempt(str(scope["path"])):
            unmet = self._unmet_minimum(Headers(scope=scope))
            if unmet is not None:
                platform, minimum = unmet
                response = error_response(
                    HTTPStatus.BAD_REQUEST,
                    CLIENT_VERSION_UNSUPPORTED,
                    _MESSAGE,
                    {"platform": platform, "minimum_version": str(minimum)},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def _unmet_minimum(self, headers: Headers) -> tuple[str, ClientVersion] | None:
        """
        Return the platform and minimum a request's app is below, if it is.

        A version that doesn't parse isn't below anything; it is logged as a
        warning instead, so a broken app build shows up in the logs.
        """
        platform = headers.get(PLATFORM_HEADER)
        version = headers.get(VERSION_HEADER)
        if platform is None or version is None:
            return None
        minimum = self.policy.minimum_for(platform)
        if minimum is None:
            return None
        parsed = ClientVersion.parse(version)
        if parsed is None:
            # The platform is one of the known three here; the version is the
            # app's raw text, so only it needs bounding.
            _logger.warning(
                "Unparseable %s from %s: %r",
                VERSION_HEADER,
                platform,
                version[:LOGGED_VERSION_LENGTH],
            )
            return None
        return None if parsed >= minimum else (platform, minimum)


def _is_exempt(path: str) -> bool:
    """Return whether `path` is a health route or a webhook route."""
    return (
        path in HEALTH_PATHS
        or path == WEBHOOKS_PREFIX
        or path.startswith(f"{WEBHOOKS_PREFIX}/")
    )
