import json
import logging
from typing import TYPE_CHECKING

import pytest
from starlette.middleware.base import BaseHTTPMiddleware

from tally_aloft.adapters.inbound.http import health
from tally_aloft.adapters.inbound.http.client_version import (
    LOGGED_VERSION_LENGTH,
    ClientVersionMiddleware,
)
from tally_aloft.adapters.inbound.http.health import HEALTH_PATHS
from tally_aloft.adapters.inbound.http.version_policy import (
    ClientPlatform,
    ClientVersion,
    ClientVersionPolicy,
)

if TYPE_CHECKING:
    from starlette.types import Message, Receive, Scope, Send

POLICY = ClientVersionPolicy({ClientPlatform.IOS: ClientVersion(1, 4, 0)})
OUTDATED = {"X-Client-Platform": "ios", "X-Client-Version": "1.3.9"}


class StubApp:
    """An ASGI app that records the scopes it was called with."""

    def __init__(self) -> None:
        self.scopes: list[Scope] = []

    async def __call__(self, scope: Scope, _receive: Receive, _send: Send) -> None:
        self.scopes.append(scope)


async def _call(
    headers: dict[str, str], path: str = "/test", scope_type: str = "http"
) -> tuple[StubApp, list[Message]]:
    """Send one request through the middleware; return the stub and what was sent."""
    stub = StubApp()
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": scope_type,
        "method": "GET",
        "path": path,
        "headers": [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in headers.items()
        ],
    }
    await ClientVersionMiddleware(stub, POLICY)(scope, receive, send)
    return stub, sent


def _passed(stub: StubApp, sent: list[Message]) -> bool:
    return len(stub.scopes) == 1 and sent == []


async def test_an_outdated_app_gets_update_required() -> None:
    stub, sent = await _call(OUTDATED)

    assert stub.scopes == []
    start, body = sent
    assert start["status"] == 400
    assert json.loads(body["body"]) == {
        "code": "client_version_unsupported",
        "message": (
            "This app version is no longer supported. Update the app to continue."
        ),
        "details": {"platform": "ios", "minimum_version": "1.4.0"},
    }


@pytest.mark.parametrize("version", ["1.4.0", "2.0.0", "1.10.0"])
async def test_an_app_at_or_above_the_minimum_passes(version: str) -> None:
    assert _passed(
        *await _call({"X-Client-Platform": "ios", "X-Client-Version": version})
    )


@pytest.mark.parametrize("platform", ["android", "web", "desktop", "iOS"])
async def test_a_platform_without_a_minimum_passes_silently(
    platform: str, caplog: pytest.LogCaptureFixture
) -> None:
    headers = {"X-Client-Platform": platform, "X-Client-Version": "0.0.1"}

    assert _passed(*await _call(headers))
    assert caplog.records == []


@pytest.mark.parametrize("version", ["banana", "", "1.4.0 (45)"])
async def test_an_unparseable_version_passes_with_one_warning(
    version: str, caplog: pytest.LogCaptureFixture
) -> None:
    headers = {"X-Client-Platform": "ios", "X-Client-Version": version}

    assert _passed(*await _call(headers))
    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert record.args == ("X-Client-Version", "ios", version)


async def test_the_logged_version_is_truncated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    version = "x" * 500

    assert _passed(
        *await _call({"X-Client-Platform": "ios", "X-Client-Version": version})
    )
    [record] = caplog.records
    assert isinstance(record.args, tuple)
    logged = record.args[2]
    assert isinstance(logged, str)
    assert len(logged) <= LOGGED_VERSION_LENGTH == 64
    assert len(record.getMessage()) < 200


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Client-Platform": "ios"},
        {"X-Client-Version": "1.3.9"},
        {},
    ],
    ids=["platform-only", "version-only", "neither"],
)
async def test_a_request_without_both_headers_passes(
    headers: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    assert _passed(*await _call(headers))
    assert caplog.records == []


@pytest.mark.parametrize(
    "path", ["/livez", "/readyz", "/webhooks", "/webhooks/test", "/webhooks/a/b"]
)
async def test_health_and_webhook_routes_are_exempt(path: str) -> None:
    assert _passed(*await _call(OUTDATED, path))


@pytest.mark.parametrize("path", ["/webhooksx", "/livez/", "/test/webhooks"])
async def test_only_exact_health_and_webhook_paths_are_exempt(path: str) -> None:
    stub, _sent = await _call(OUTDATED, path)

    assert stub.scopes == []


@pytest.mark.parametrize("scope_type", ["websocket", "lifespan"])
async def test_other_scopes_pass_through(scope_type: str) -> None:
    assert _passed(*await _call(OUTDATED, scope_type=scope_type))


def test_the_exempt_health_paths_are_the_health_routes() -> None:
    paths = {getattr(route, "path", None) for route in health.router.routes}

    assert paths == HEALTH_PATHS


def test_the_middleware_is_plain_asgi() -> None:
    assert not issubclass(ClientVersionMiddleware, BaseHTTPMiddleware)
