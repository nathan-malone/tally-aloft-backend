from typing import NoReturn, Self, override

import pytest
from docker.errors import APIError
from testcontainers.core import container
from testcontainers.core.container import DockerContainer

from tests.support.containers import started

PORT_TAKEN = APIError(
    "500 Server Error",
    explanation="failed to listen on TCP socket: address already in use",
)
OTHER = APIError("500 Server Error", explanation="no such image")


@pytest.fixture(autouse=True)
def no_docker_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail a test here that builds a Docker client, which would need Docker."""

    def refuse(**_kwargs: object) -> NoReturn:
        pytest.fail("a fake container built a Docker client")

    monkeypatch.setattr(container, "DockerClient", refuse)


class FakeContainer(DockerContainer):
    """A container that fails to start with the errors it is given, in order."""

    # `started` calls only start and stop, which the fake overrides.
    # pyrefly: ignore[missing-super-call]  # The base's __init__ connects to Docker.
    def __init__(self, errors: list[APIError], log: list[str]) -> None:
        self.errors = errors
        self.log = log

    @override
    def start(self) -> Self:
        self.log.append("start")
        if len(self.errors) > 0:
            raise self.errors.pop(0)
        return self

    @override
    def stop(self, force: bool = True, delete_volume: bool = True) -> None:
        self.log.append("stop")


def test_a_taken_host_port_is_retried_with_a_new_container() -> None:
    errors = [PORT_TAKEN]
    log: list[str] = []
    made: list[FakeContainer] = []

    def make() -> FakeContainer:
        made.append(FakeContainer(errors, log))
        return made[-1]

    with started(make) as container:
        assert container is made[-1]

    assert len(made) == 2
    assert log == ["start", "stop", "start", "stop"]


def test_another_start_error_is_raised_at_once() -> None:
    errors = [OTHER, PORT_TAKEN]
    log: list[str] = []

    with (
        pytest.raises(APIError, match="no such image"),
        started(lambda: FakeContainer(errors, log)),
    ):
        pass

    assert log == ["start", "stop"]


def test_a_port_that_stays_taken_fails_after_three_starts() -> None:
    errors = [PORT_TAKEN, PORT_TAKEN, PORT_TAKEN]
    log: list[str] = []

    with (
        pytest.raises(APIError, match="address already in use"),
        started(lambda: FakeContainer(errors, log)),
    ):
        pass

    assert log == ["start", "stop"] * 3
