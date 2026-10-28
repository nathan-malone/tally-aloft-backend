import time
from typing import TYPE_CHECKING

import pytest

from tally_aloft.bootstrap.app import create_app
from tally_aloft.bootstrap.settings import Settings
from tests.support.http import request
from tests.support.server import free_port, silent_server

if TYPE_CHECKING:
    import httpx

    from tests.support.postgres import PostgresServer

# NFR-6: /readyz answers within 3 seconds even when a dependency is down.
READYZ_DEADLINE_SECONDS = 3


async def _readyz(database_url: str) -> httpx.Response:
    """Call /readyz through the real lifespan and the real readiness checks."""
    app = create_app(Settings.for_tests(database_url=database_url))
    async with app.router.lifespan_context(app):
        return await request(app, "GET", "/readyz")


def _assert_postgres_not_ready(response: httpx.Response) -> None:
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "not_ready"
    assert body["details"] == {"failed_checks": ["postgres"]}


@pytest.mark.container
async def test_ready_when_postgres_answers(postgres_server: PostgresServer) -> None:
    response = await _readyz(postgres_server.app_url)

    assert response.status_code == 200


async def test_not_ready_when_postgres_refuses_connections() -> None:
    started = time.monotonic()

    response = await _readyz(f"postgresql+asyncpg://app@127.0.0.1:{free_port()}/db")

    assert time.monotonic() - started < READYZ_DEADLINE_SECONDS
    _assert_postgres_not_ready(response)


async def test_not_ready_when_postgres_never_answers() -> None:
    async with silent_server() as port:
        started = time.monotonic()

        response = await _readyz(f"postgresql+asyncpg://app@127.0.0.1:{port}/db")

        assert time.monotonic() - started < READYZ_DEADLINE_SECONDS
    _assert_postgres_not_ready(response)
