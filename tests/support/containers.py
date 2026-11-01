import shutil
import time
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import pytest
from docker.errors import APIError

from tests.support.server import STARTUP_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from docker.models.containers import Container
    from testcontainers.core.container import DockerContainer

# CI builds the image once and names it here; locally a fixture builds one.
IMAGE_VARIABLE = "BACKEND_TEST_IMAGE"
LOCAL_TAG = "tally-aloft-backend:test"
# How a container reaches a port published on the host. Containers started by
# the tests map it with `extra_hosts`, which Docker Desktop and Linux both take.
HOST_FROM_CONTAINER = "host.docker.internal"
HOST_GATEWAY = {HOST_FROM_CONTAINER: "host-gateway"}
# Docker's answer when the host port it chose to publish is already held.
_PORT_TAKEN = "address already in use"
_START_ATTEMPTS = 3


def docker_cli() -> str:
    """Return the path of the docker CLI, or fail the test that needs it."""
    path = shutil.which("docker")
    if path is None:
        pytest.fail("this test needs the docker CLI on PATH")
    return path


def unique_name(purpose: str) -> str:
    """Return a container or network name no other test run uses."""
    return f"tally-aloft-test-{purpose}-{uuid.uuid4().hex[:8]}"


def wait_for_log(container: Container, text: str) -> None:
    """Wait until `text` appears in the container's output."""
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if text in container.logs().decode():
            return
        container.reload()
        if container.status == "exited":
            break
        time.sleep(0.2)
    logs = container.logs().decode()
    msg = f"{text!r} never appeared in the container's output:\n{logs}"
    raise AssertionError(msg)


def from_container(url: str) -> str:
    """Return `url`, a server published on this host, as a container reaches it."""
    parts = urlsplit(url)
    credentials, _, _ = parts.netloc.rpartition("@")
    host = (
        HOST_FROM_CONTAINER
        if parts.port is None
        else f"{HOST_FROM_CONTAINER}:{parts.port}"
    )
    netloc = f"{credentials}@{host}" if credentials != "" else host
    return urlunsplit(parts._replace(netloc=netloc))


@contextmanager
def started[ContainerT: DockerContainer](
    make: Callable[[], ContainerT],
) -> Generator[ContainerT]:
    """Start the container `make` builds, and remove it on exit."""
    container = _start_retrying(
        make, lambda made: made.start(), lambda made: made.stop()
    )
    try:
        yield container
    finally:
        container.stop()


def start_retrying[ContainerT: Container](
    create: Callable[[], ContainerT],
) -> ContainerT:
    """Start the Docker SDK container `create` creates, as `started` does."""
    return _start_retrying(
        create, lambda made: made.start(), lambda made: made.remove(force=True)
    )


def _start_retrying[ContainerT](
    make: Callable[[], ContainerT],
    start: Callable[[ContainerT], object],
    discard: Callable[[ContainerT], object],
) -> ContainerT:
    """
    Start the container `make` builds, and return it.

    Docker picks a free ephemeral host port for each published port, but it
    can't see a port that another socket holds at that moment, such as a
    client's outgoing connection on a busy CI runner. The start then fails
    with "address already in use". Each retry discards the container and
    builds a new one, so Docker picks again. Any other failure discards the
    container and is raised at once.
    """
    attempt = 1
    while True:
        container = make()
        try:
            _ = start(container)
        except BaseException as error:
            _ = discard(container)
            port_taken = isinstance(error, APIError) and _PORT_TAKEN in str(error)
            if not port_taken or attempt == _START_ATTEMPTS:
                raise
            attempt += 1
        else:
            return container
