import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from tests.support.containers import unique_name, wait_for_log
from tests.support.server import REQUIRED_SETTINGS

if TYPE_CHECKING:
    from collections.abc import Callable

    import docker
    from docker.models.containers import Container
    from docker.models.images import Image
    from docker.models.networks import Network
    from docker.types import Mount

pytestmark = pytest.mark.container

API_INSIDE = "10.1.2.10"
API_OUTSIDE = "192.168.240.10"
# Retries until the API answers, then prints the response body.
CLIENT_SCRIPT = """
import sys, time, urllib.request
url, forwarded_for = sys.argv[1], sys.argv[2]
request = urllib.request.Request(url, headers={"X-Forwarded-For": forwarded_for})
deadline = time.monotonic() + 60
while True:
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            print(response.read().decode())
            break
    except OSError:
        if time.monotonic() > deadline:
            raise
        time.sleep(0.5)
"""


@dataclass(frozen=True)
class Lab:
    """Creates containers from the image under test, at fixed addresses."""

    client: docker.DockerClient
    image: Image
    started: list[Container]

    def create(
        self,
        command: list[str],
        network: Network,
        address: str,
        *,
        environment: dict[str, str] | None = None,
        mounts: list[Mount] | None = None,
    ) -> Container:
        name = str(network.name)
        container = self.client.containers.create(
            self.image,
            command,
            name=unique_name("container"),
            environment=environment,
            mounts=mounts,
            network=name,
            networking_config={
                name: self.client.api.create_endpoint_config(ipv4_address=address)
            },
        )
        self.started.append(container)
        return container

    def client_sees(self, network: Network, address: str, url: str) -> str:
        """Return the client address the API reports for a request from `address`."""
        command = ["python", "-c", CLIENT_SCRIPT, url, "1.2.3.4"]
        container = self.create(command, network, address)
        container.start()
        result = container.wait(timeout=90)
        assert result["StatusCode"] == 0, container.logs().decode()
        return str(json.loads(container.logs(stdout=True, stderr=False).decode()))


@pytest.fixture
def lab(
    docker_client: docker.DockerClient,
    api_image: Image,
    started_containers: list[Container],
) -> Lab:
    return Lab(docker_client, api_image, started_containers)


def test_forwarded_for_is_trusted_only_from_the_configured_network(
    lab: Lab,
    probe_command: list[str],
    tests_mount: Mount,
    make_network: Callable[[str], Network],
) -> None:
    inside = make_network("10.1.2.0/24")
    outside = make_network("192.168.240.0/24")
    api = lab.create(
        probe_command,
        inside,
        API_INSIDE,
        environment={**REQUIRED_SETTINGS, "TALLY_FORWARDED_ALLOW_IPS": "10.0.0.0/8"},
        mounts=[tests_mount],
    )
    outside.connect(api, ipv4_address=API_OUTSIDE)
    api.start()
    wait_for_log(api, "Application startup complete")

    url = "http://{}:8000/test/client"
    from_inside = lab.client_sees(inside, "10.1.2.3", url.format(API_INSIDE))
    from_outside = lab.client_sees(outside, "192.168.240.3", url.format(API_OUTSIDE))

    assert from_inside == "1.2.3.4"
    assert from_outside == "192.168.240.3"
