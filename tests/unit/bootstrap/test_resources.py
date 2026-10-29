import socket
from typing import NoReturn

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from tally_aloft.bootstrap.resources import open_resources
from tally_aloft.bootstrap.settings import Settings


@pytest.fixture
def disposed(monkeypatch: pytest.MonkeyPatch) -> list[AsyncEngine]:
    """Record each engine that is disposed, instead of disposing it."""
    engines: list[AsyncEngine] = []

    async def record(self: AsyncEngine, *, close: bool = True) -> None:  # noqa: ARG001  # It mirrors dispose's signature.
        engines.append(self)

    monkeypatch.setattr(AsyncEngine, "dispose", record)
    return engines


@pytest.fixture
def closed(monkeypatch: pytest.MonkeyPatch) -> list[Redis]:
    """Record each Valkey client that is closed, instead of closing it."""
    clients: list[Redis] = []

    async def record(self: Redis, close_connection_pool: bool | None = None) -> None:  # noqa: ARG001, FBT001  # It mirrors aclose's signature.
        clients.append(self)

    monkeypatch.setattr(Redis, "aclose", record)
    return clients


async def test_disposes_the_engine_on_exit(disposed: list[AsyncEngine]) -> None:
    async with open_resources(Settings.for_tests()) as resources:
        assert disposed == []

    assert disposed == [resources.engine]


async def test_disposes_the_engine_when_the_body_raises(
    disposed: list[AsyncEngine],
) -> None:
    msg = "body failed"

    with pytest.raises(RuntimeError, match=msg):
        async with open_resources(Settings.for_tests()):
            raise RuntimeError(msg)

    assert len(disposed) == 1


async def test_closes_the_cache_client_on_exit(closed: list[Redis]) -> None:
    async with open_resources(Settings.for_tests()) as resources:
        assert closed == []

    assert closed == [resources.cache_client]


async def test_closes_the_cache_client_when_the_body_raises(
    closed: list[Redis],
) -> None:
    msg = "body failed"

    with pytest.raises(RuntimeError, match=msg):
        async with open_resources(Settings.for_tests()):
            raise RuntimeError(msg)

    assert len(closed) == 1


async def test_opens_no_socket_on_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        msg = "open_resources opened a socket"
        raise AssertionError(msg)

    with monkeypatch.context() as patch:
        patch.setattr(socket, "socket", refuse)
        async with open_resources(Settings.for_tests()) as resources:
            assert isinstance(resources.engine, AsyncEngine)
            assert isinstance(resources.cache_client, Redis)
