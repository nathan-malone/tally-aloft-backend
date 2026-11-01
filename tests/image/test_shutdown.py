import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

import httpx
import pytest

from tests.support.containers import start_retrying, unique_name, wait_for_log
from tests.support.server import REQUIRED_SETTINGS

if TYPE_CHECKING:
    from collections.abc import Callable

    import docker
    from docker.models.containers import Container
    from docker.models.images import Image
    from docker.types import Mount

pytestmark = pytest.mark.container

DRAIN_FLAG = "--timeout-graceful-shutdown"
# The exit test shortens the image's 25-second drain, so it doesn't wait it out;
# test_image pins the real value. The exit limit keeps the image contract's
# headroom: a 25-second drain inside a 30-second stop.
TEST_DRAIN_SECONDS = 2
MAX_EXIT_SECONDS = TEST_DRAIN_SECONDS + 5


@pytest.fixture
def start_api(
    docker_client: docker.DockerClient,
    api_image: Image,
    tests_mount: Mount,
    started_containers: list[Container],
) -> Callable[[list[str]], Container]:
    def start(command: list[str]) -> Container:
        container = start_retrying(
            lambda: docker_client.containers.create(
                api_image,
                command,
                name=unique_name("api"),
                environment=REQUIRED_SETTINGS,
                mounts=[tests_mount],
                # Port 0: Docker picks a free one.
                ports={"8000/tcp": ("127.0.0.1", 0)},
            )
        )
        started_containers.append(container)
        wait_for_log(container, "Application startup complete")
        container.reload()
        return container

    return start


def _with_drain_seconds(command: list[str], seconds: int) -> list[str]:
    """Return `command` with its graceful-shutdown timeout set to `seconds`."""
    value = command.index(DRAIN_FLAG) + 1
    return [*command[:value], str(seconds), *command[value + 1 :]]


def _base_url(container: Container) -> str:
    bindings = container.ports["8000/tcp"]
    assert bindings is not None
    port = bindings[0]["HostPort"]
    return f"http://127.0.0.1:{port}"


async def test_in_flight_request_completes_after_sigterm(
    start_api: Callable[[list[str]], Container], probe_command: list[str]
) -> None:
    api = start_api(probe_command)
    async with httpx.AsyncClient(base_url=_base_url(api), timeout=30) as client:
        request = asyncio.create_task(client.get("/test/sleep", params={"seconds": 2}))
        await asyncio.to_thread(wait_for_log, api, "sleep started")
        api.kill(signal="SIGTERM")
        response = await request

    assert response.status_code == 200


async def test_process_exits_when_the_drain_timeout_ends(
    start_api: Callable[[list[str]], Container], probe_command: list[str]
) -> None:
    api = start_api(_with_drain_seconds(probe_command, TEST_DRAIN_SECONDS))
    async with httpx.AsyncClient(base_url=_base_url(api), timeout=90) as client:
        request = asyncio.create_task(client.get("/test/sleep", params={"seconds": 60}))
        await asyncio.to_thread(wait_for_log, api, "sleep started")
        signalled = time.monotonic()
        api.kill(signal="SIGTERM")
        await asyncio.to_thread(api.wait, timeout=MAX_EXIT_SECONDS + 10)
        elapsed = time.monotonic() - signalled
        request.cancel()
        with contextlib.suppress(asyncio.CancelledError, httpx.HTTPError):
            await request

    assert elapsed <= MAX_EXIT_SECONDS
