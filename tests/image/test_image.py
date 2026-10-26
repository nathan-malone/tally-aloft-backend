from typing import TYPE_CHECKING

import pytest
from docker.errors import ContainerError

if TYPE_CHECKING:
    import docker
    from docker.models.images import Image

pytestmark = pytest.mark.container

API_COMMAND = [
    "uvicorn",
    "tally_aloft.main:app",
    "--host",
    "0.0.0.0",  # noqa: S104  # The container's port must be reachable.
    "--port",
    "8000",
    "--no-proxy-headers",
    "--timeout-graceful-shutdown",
    "25",
]
MAX_UNCOMPRESSED_BYTES = 400 * 1000 * 1000
NO_TESTS = (
    "import importlib.util, pathlib; "
    "print(pathlib.Path('/app/tests').exists(), "
    "importlib.util.find_spec('tests') is not None)"
)


def _run(
    client: docker.DockerClient, image: Image, command: list[str], user: str = ""
) -> str:
    output = client.containers.run(image, command, remove=True, user=user)
    return output.decode().strip()


def test_default_command_is_the_documented_api_command(api_image: Image) -> None:
    assert api_image.attrs["Config"]["Cmd"] == API_COMMAND


def test_runs_as_a_non_root_user(
    docker_client: docker.DockerClient, api_image: Image
) -> None:
    assert api_image.attrs["Config"]["User"] == "10001"
    assert _run(docker_client, api_image, ["id", "-u"]) != "0"


def test_is_at_most_400_mb_uncompressed(
    docker_client: docker.DockerClient, api_image: Image
) -> None:
    usage = _run(docker_client, api_image, ["du", "-sxb", "/"], user="0")

    assert int(usage.split()[0]) <= MAX_UNCOMPRESSED_BYTES


def test_contains_no_env_file(
    docker_client: docker.DockerClient, api_image: Image
) -> None:
    command = ["find", "/", "-xdev", "-name", ".env"]

    assert _run(docker_client, api_image, command, user="0") == ""


def test_contains_no_tests(
    docker_client: docker.DockerClient, api_image: Image
) -> None:
    assert _run(docker_client, api_image, ["python", "-c", NO_TESTS]) == "False False"


def test_pytest_is_not_installed(
    docker_client: docker.DockerClient, api_image: Image
) -> None:
    with pytest.raises(ContainerError):
        _run(docker_client, api_image, ["python", "-c", "import pytest"])
