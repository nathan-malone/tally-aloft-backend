import time
from typing import TYPE_CHECKING

import pytest

from tally_aloft.bootstrap.app import create_app
from tally_aloft.bootstrap.settings import UNUSED_VALKEY_URL, Settings
from tests.support.http import request
from tests.support.server import free_port, silent_server

if TYPE_CHECKING:
    import httpx

    from tests.support.postgres import PostgresDatabase
    from tests.support.valkey import ValkeyServer

# NFR-6: /readyz answers within 3 seconds even when a dependency is down.
READYZ_DEADLINE_SECONDS = 3


async def _readyz(*, database_url: str, valkey_url: str) -> httpx.Response:
    """Call /readyz through the real lifespan and the real readiness checks."""
    settings = Settings.for_tests(database_url=database_url, valkey_url=valkey_url)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        return await request(app, "GET", "/readyz")


def _assert_not_ready(response: httpx.Response, failed_checks: list[str]) -> None:
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "not_ready"
    assert body["details"] == {"failed_checks": failed_checks}


@pytest.mark.container
async def test_ready_when_postgres_and_valkey_answer(
    postgres_server: PostgresDatabase, valkey_server: ValkeyServer
) -> None:
    response = await _readyz(
        database_url=postgres_server.app_url, valkey_url=valkey_server.url
    )

    assert response.status_code == 200


@pytest.mark.container
async def test_not_ready_when_postgres_refuses_connections(
    valkey_server: ValkeyServer,
) -> None:
    started = time.monotonic()

    response = await _readyz(
        database_url=f"postgresql+asyncpg://app@127.0.0.1:{free_port()}/db",
        valkey_url=valkey_server.url,
    )

    assert time.monotonic() - started < READYZ_DEADLINE_SECONDS
    _assert_not_ready(response, ["postgres"])


@pytest.mark.container
async def test_not_ready_when_postgres_never_answers(
    valkey_server: ValkeyServer,
) -> None:
    async with silent_server() as port:
        started = time.monotonic()

        response = await _readyz(
            database_url=f"postgresql+asyncpg://app@127.0.0.1:{port}/db",
            valkey_url=valkey_server.url,
        )

        assert time.monotonic() - started < READYZ_DEADLINE_SECONDS
    _assert_not_ready(response, ["postgres"])


@pytest.mark.container
async def test_not_ready_when_valkey_refuses_connections(
    postgres_server: PostgresDatabase,
) -> None:
    started = time.monotonic()

    response = await _readyz(
        database_url=postgres_server.app_url,
        valkey_url=f"redis://127.0.0.1:{free_port()}/0",
    )

    assert time.monotonic() - started < READYZ_DEADLINE_SECONDS
    _assert_not_ready(response, ["cache"])


@pytest.mark.container
async def test_not_ready_when_valkey_never_answers(
    postgres_server: PostgresDatabase,
) -> None:
    async with silent_server() as port:
        started = time.monotonic()

        response = await _readyz(
            database_url=postgres_server.app_url,
            valkey_url=f"redis://127.0.0.1:{port}/0",
        )

        assert time.monotonic() - started < READYZ_DEADLINE_SECONDS
    _assert_not_ready(response, ["cache"])


async def test_not_ready_names_every_failed_check_in_order() -> None:
    async with silent_server() as port:
        started = time.monotonic()

        response = await _readyz(
            database_url=f"postgresql+asyncpg://app@127.0.0.1:{port}/db",
            valkey_url=UNUSED_VALKEY_URL,
        )

        assert time.monotonic() - started < READYZ_DEADLINE_SECONDS
    _assert_not_ready(response, ["postgres", "cache"])
