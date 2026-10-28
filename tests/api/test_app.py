import socket
from typing import NoReturn

import httpx
import pytest
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from tally_aloft.adapters.inbound.http.errors import UnhandledErrorMiddleware
from tally_aloft.adapters.outbound.postgres.health import PostgresCheck
from tally_aloft.application.registry import UseCases
from tally_aloft.application.shared.check_readiness import CheckReadiness
from tally_aloft.bootstrap.app import create_app
from tally_aloft.bootstrap.settings import Settings
from tests.support import probe_routes


def test_create_app_opens_no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings.for_tests()

    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        msg = "create_app opened a socket"
        raise AssertionError(msg)

    with monkeypatch.context() as patch:
        patch.setattr(socket, "socket", refuse)
        app = create_app(settings)

    assert isinstance(app, FastAPI)


async def test_lifespan_sets_the_use_cases() -> None:
    app = create_app(Settings.for_tests())

    async with app.router.lifespan_context(app):
        use_cases = app.state.use_cases
        assert isinstance(use_cases, UseCases)
        assert isinstance(use_cases.check_readiness, CheckReadiness)
        checks = use_cases.check_readiness.checks
        assert [type(check) for check in checks] == [PostgresCheck]


async def test_livez_returns_200_and_is_not_in_the_schema() -> None:
    app = create_app(Settings.for_tests())
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        response = await client.get("/livez")

    assert response.status_code == 200
    assert "/livez" not in app.openapi().get("paths", {})


def test_middleware_order_from_the_outside_in() -> None:
    app = create_app(Settings.for_tests())

    assert [middleware.cls for middleware in app.user_middleware] == [
        ProxyHeadersMiddleware,
        CORSMiddleware,
        UnhandledErrorMiddleware,
    ]


@pytest.mark.parametrize(
    ("trusted", "peer", "forwarded_for", "seen"),
    [
        ("10.0.0.0/8", "10.1.2.3", "1.2.3.4", "1.2.3.4"),
        ("10.0.0.0/8", "10.1.2.3", "6.6.6.6, 1.2.3.4", "1.2.3.4"),
        ("10.0.0.0/8", "192.168.1.5", "1.2.3.4", "192.168.1.5"),
        ("", "10.1.2.3", "1.2.3.4", "10.1.2.3"),
    ],
    ids=["trusted", "spoofed-chain", "untrusted", "nothing-trusted"],
)
async def test_forwarded_for_is_trusted_only_from_configured_proxies(
    trusted: str, peer: str, forwarded_for: str, seen: str
) -> None:
    app = create_app(Settings.for_tests(forwarded_allow_ips=trusted))
    app.include_router(probe_routes.router)
    transport = httpx.ASGITransport(app=app, client=(peer, 50000))

    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        response = await client.get(
            "/test/client", headers={"X-Forwarded-For": forwarded_for}
        )

    assert response.json() == seen
