import socket
from typing import NoReturn

import pytest
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


async def test_opens_no_socket_on_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        msg = "open_resources opened a socket"
        raise AssertionError(msg)

    with monkeypatch.context() as patch:
        patch.setattr(socket, "socket", refuse)
        async with open_resources(Settings.for_tests()) as resources:
            assert isinstance(resources.engine, AsyncEngine)
