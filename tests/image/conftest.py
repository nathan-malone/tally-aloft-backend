import os
import subprocess
from typing import TYPE_CHECKING

import docker
import pytest
from docker.types import IPAMConfig, IPAMPool, Mount

from tests.support.containers import IMAGE_VARIABLE, LOCAL_TAG, docker_cli, unique_name
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
def api_image(docker_client: docker.DockerClient) -> Image:
    tag = os.environ.get(IMAGE_VARIABLE)
    if tag is None:
        subprocess.run(
            [docker_cli(), "build", "-t", LOCAL_TAG, "."], cwd=ROOT, check=True
        )
        tag = LOCAL_TAG
    return docker_client.images.get(tag)


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
