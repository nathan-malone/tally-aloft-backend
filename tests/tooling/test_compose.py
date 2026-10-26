import json
import shutil
import subprocess
from typing import cast

import pytest

from tally_aloft.bootstrap.settings import Settings, variable_name
from tests.support.paths import ROOT

pytestmark = pytest.mark.container


@pytest.fixture(scope="module")
def api_service() -> dict[str, object]:
    """Return the `api` service as Compose renders it under profile `app`."""
    docker_cli = shutil.which("docker")
    if docker_cli is None:
        pytest.fail("the Compose tests need the docker CLI on PATH")
    result = subprocess.run(
        [docker_cli, "compose", "--profile", "app", "config", "--format", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    services = cast(
        "dict[str, dict[str, object]]", json.loads(result.stdout)["services"]
    )
    return services["api"]


def test_api_runs_only_in_the_app_profile(api_service: dict[str, object]) -> None:
    assert api_service["profiles"] == ["app"]


def test_api_port_is_published_on_localhost_only(
    api_service: dict[str, object],
) -> None:
    ports = cast("list[dict[str, object]]", api_service["ports"])

    assert [(port["host_ip"], port["published"], port["target"]) for port in ports] == [
        ("127.0.0.1", "8000", 8000)
    ]


def test_api_health_check_uses_python_against_livez(
    api_service: dict[str, object],
) -> None:
    test = cast(
        "list[str]", cast("dict[str, object]", api_service["healthcheck"])["test"]
    )

    assert test[:2] == ["CMD", "python"]
    assert "http://127.0.0.1:8000/livez" in test[-1]


def test_api_has_time_to_drain_before_it_is_killed(
    api_service: dict[str, object],
) -> None:
    assert api_service["stop_grace_period"] == "30s"


def test_api_sets_every_required_setting(api_service: dict[str, object]) -> None:
    environment = cast("dict[str, str]", api_service["environment"])
    required = {
        variable_name(name)
        for name, field in Settings.model_fields.items()
        if field.is_required()
    }

    assert required <= environment.keys()
