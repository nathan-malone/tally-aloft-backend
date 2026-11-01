import os
import subprocess
from typing import TYPE_CHECKING

import docker
import pytest
from docker.types import IPAMConfig, IPAMPool, Mount
from filelock import FileLock

from tests.support.containers import (
    HOST_GATEWAY,
    IMAGE_VARIABLE,
    LOCAL_TAG,
    docker_cli,
    unique_name,
)
from tests.support.paths import ROOT
from tests.support.server import API_APP, PROBE_APP

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from docker.models.containers import Container
    from docker.models.images import Image
    from docker.models.networks import Network


@pytest.fixture(scope="session")
def docker_client() -> Generator[docker.DockerClient]:
    client = docker.from_env()
    yield client
    client.close()


@pytest.fixture(scope="session")
def api_image(
    docker_client: docker.DockerClient, tmp_path_factory: pytest.TempPathFactory
) -> Image:
    tag = os.environ.get(IMAGE_VARIABLE)
    if tag is not None:
        return docker_client.images.get(tag)
    if os.environ.get("PYTEST_XDIST_WORKER") is None:
        return _build_image(docker_client)
    # Every build gets a new image ID and moves the tag, and Docker can remove
    # the image the tag moved from, so the xdist workers share one build by ID.
    # The parent of a worker's base temp directory is shared by this run only.
    shared = tmp_path_factory.getbasetemp().parent
    built = shared / "image-id"
    with FileLock(shared / "image.lock"):
        if not built.exists():
            built.write_text(str(_build_image(docker_client).id))
    return docker_client.images.get(built.read_text())


def _build_image(docker_client: docker.DockerClient) -> Image:
    subprocess.run([docker_cli(), "build", "-t", LOCAL_TAG, "."], cwd=ROOT, check=True)
    return docker_client.images.get(LOCAL_TAG)


@pytest.fixture(scope="session")
def probe_command(api_image: Image) -> list[str]:
    """Return the image's default command, serving the probe app instead."""
    command: list[str] = api_image.attrs["Config"]["Cmd"]
    assert command.count(API_APP) == 1
    return [PROBE_APP if part == API_APP else part for part in command]


@pytest.fixture
def tests_mount() -> Mount:
    """Mount this repository's tests read-only where the probe app imports them."""
    return Mount(
        target="/app/tests", source=str(ROOT / "tests"), type="bind", read_only=True
    )


@pytest.fixture
def make_network(
    docker_client: docker.DockerClient,
) -> Generator[Callable[[str], Network]]:
    created: list[Network] = []

    def make(subnet: str) -> Network:
        network = docker_client.networks.create(
            unique_name("net"),
            driver="bridge",
            ipam=IPAMConfig(pool_configs=[IPAMPool(subnet=subnet)]),
        )
        created.append(network)
        return network

    yield make
    for network in created:
        # Containers may outlive this fixture, so detach them first.
        network.reload()
        for container in network.containers:
            network.disconnect(container, force=True)
        network.remove()


@pytest.fixture
def started_containers() -> Generator[list[Container]]:
    """Collect the containers a test starts, and remove them afterwards."""
    containers: list[Container] = []
    yield containers
    for container in containers:
        container.remove(force=True)


@pytest.fixture
def start_worker(
    docker_client: docker.DockerClient,
    api_image: Image,
    tests_mount: Mount,
    started_containers: list[Container],
) -> Callable[[list[str], dict[str, str]], Container]:
    """
    Start a container running `command` from the image, with `tests/` mounted.

    It reaches servers published on this host through `host.docker.internal`.
    It returns at once; a test that needs the worker listening waits for that.
    """

    def start(command: list[str], environment: dict[str, str]) -> Container:
        container = _run_with_tests(
            docker_client,
            api_image,
            tests_mount,
            purpose="worker",
            command=command,
            environment=environment,
        )
        started_containers.append(container)
        return container

    return start


@pytest.fixture
def start_scheduler(
    docker_client: docker.DockerClient,
    api_image: Image,
    tests_mount: Mount,
    started_containers: list[Container],
) -> Callable[[list[str], dict[str, str]], Container]:
    """
    Start a scheduler container, as `start_worker` does, under an init process.

    Compose runs the scheduler with `init: true`, because Taskiq's scheduler
    has no `SIGTERM` handler, which PID 1 needs to stop on one.
    """

    def start(command: list[str], environment: dict[str, str]) -> Container:
        container = _run_with_tests(
            docker_client,
            api_image,
            tests_mount,
            purpose="scheduler",
            command=command,
            environment=environment,
            init=True,
        )
        started_containers.append(container)
        return container

    return start


def _run_with_tests(  # noqa: PLR0913  # The container's settings, one by one.
    docker_client: docker.DockerClient,
    image: Image,
    tests_mount: Mount,
    *,
    purpose: str,
    command: list[str],
    environment: dict[str, str],
    init: bool = False,
) -> Container:
    """Run `command` from `image`, detached, with `tests/` mounted."""
    return docker_client.containers.run(
        image,
        command,
        detach=True,
        name=unique_name(purpose),
        environment=environment,
        mounts=[tests_mount],
        extra_hosts=HOST_GATEWAY,
        init=init,
    )
