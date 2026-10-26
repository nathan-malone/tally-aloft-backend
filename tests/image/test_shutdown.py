import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

import httpx
import pytest

from tests.support.containers import unique_name, wait_for_log

if TYPE_CHECKING:
    from collections.abc import Generator

    import docker
    from docker.models.containers import Container
    from docker.models.images import Image
    from docker.types import Mount

pytestmark = pytest.mark.container

MAX_EXIT_SECONDS = 30


@pytest.fixture
def api(
    docker_client: docker.DockerClient,
    api_image: Image,
    probe_command: list[str],
    tests_mount: Mount,
) -> Generator[Container]:
    container = docker_client.containers.run(
        api_image,
        probe_command,
        detach=True,
        name=unique_name("api"),
        environment={"TALLY_ENVIRONMENT": "test"},
        mounts=[tests_mount],
        ports={"8000/tcp": ("127.0.0.1", 0)},  # Port 0: Docker picks a free one.
    )
    try:
        wait_for_log(container, "Application startup complete")
        container.reload()
        yield container
    finally:
        container.remove(force=True)


def _base_url(container: Container) -> str:
    bindings = container.ports["8000/tcp"]
    assert bindings is not None
    port = bindings[0]["HostPort"]
    return f"http://127.0.0.1:{port}"


async def test_in_flight_request_completes_after_sigterm(api: Container) -> None:
    async with httpx.AsyncClient(base_url=_base_url(api), timeout=30) as client:
        request = asyncio.create_task(client.get("/test/sleep", params={"seconds": 2}))
        await asyncio.to_thread(wait_for_log, api, "sleep started")
        api.kill(signal="SIGTERM")
        response = await request

    assert response.status_code == 200


async def test_process_exits_within_30_seconds_of_sigterm(api: Container) -> None:
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
