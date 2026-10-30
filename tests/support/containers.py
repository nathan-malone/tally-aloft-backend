import shutil
import time
import uuid
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import pytest

from tests.support.server import STARTUP_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from docker.models.containers import Container

# CI builds the image once and names it here; locally a fixture builds one.
IMAGE_VARIABLE = "BACKEND_TEST_IMAGE"
LOCAL_TAG = "tally-aloft-backend:test"
# How a container reaches a port published on the host. Containers started by
# the tests map it with `extra_hosts`, which Docker Desktop and Linux both take.
HOST_FROM_CONTAINER = "host.docker.internal"
HOST_GATEWAY = {HOST_FROM_CONTAINER: "host-gateway"}


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
