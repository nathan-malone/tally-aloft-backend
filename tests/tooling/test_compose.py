import json
import subprocess
from typing import cast

import pytest

from tally_aloft.bootstrap.settings import MigrationSettings, Settings, variable_name
from tests.support.containers import docker_cli
from tests.support.paths import ROOT
from tests.support.postgres import POSTGRES_IMAGE
from tests.support.valkey import VALKEY_IMAGE
from tests.support.worker_command import (
    TEST_WORKER_MODULE,
    WORKER_MODULE,
    worker_command,
)

pytestmark = pytest.mark.container

Service = dict[str, object]


def _rendered(*files: str) -> dict[str, Service]:
    """Return every service as Compose renders `files` under profile `app`."""
    options = [part for file in files for part in ("-f", file)]
    result = subprocess.run(
        [
            docker_cli(),
            "compose",
            *options,
            "--profile",
            "app",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return cast("dict[str, Service]", json.loads(result.stdout)["services"])


@pytest.fixture(scope="module")
def services() -> dict[str, Service]:
    """Return every service as Compose renders compose.yaml under profile `app`."""
    return _rendered("compose.yaml")


@pytest.fixture(scope="module")
def worker_service(services: dict[str, Service]) -> Service:
    return services["worker"]


@pytest.fixture(scope="module")
def api_service(services: dict[str, Service]) -> Service:
    return services["api"]


def _ports(service: Service) -> list[tuple[object, object, object]]:
    ports = cast("list[dict[str, object]]", service["ports"])
    return [(port["host_ip"], port["published"], port["target"]) for port in ports]


def _environment(service: Service) -> dict[str, str]:
    return cast("dict[str, str]", service["environment"])


def _depends_on(service: Service) -> dict[str, str]:
    depends_on = cast("dict[str, dict[str, object]]", service["depends_on"])
    return {name: str(rule["condition"]) for name, rule in depends_on.items()}


def _required_variables(model: type[Settings | MigrationSettings]) -> set[str]:
    return {
        variable_name(name)
        for name, field in model.model_fields.items()
        if field.is_required()
    }


def test_api_runs_only_in_the_app_profile(api_service: Service) -> None:
    assert api_service["profiles"] == ["app"]


def test_api_port_is_published_on_localhost_only(api_service: Service) -> None:
    assert _ports(api_service) == [("127.0.0.1", "8000", 8000)]


def test_api_health_check_uses_python_against_livez(api_service: Service) -> None:
    test = cast(
        "list[str]", cast("dict[str, object]", api_service["healthcheck"])["test"]
    )

    assert test[:2] == ["CMD", "python"]
    assert "http://127.0.0.1:8000/livez" in test[-1]


def test_api_has_time_to_drain_before_it_is_killed(api_service: Service) -> None:
    assert api_service["stop_grace_period"] == "30s"


def test_api_sets_every_required_setting(api_service: Service) -> None:
    assert _required_variables(Settings) <= _environment(api_service).keys()


def test_api_never_gets_the_owner_role(api_service: Service) -> None:
    variables = _environment(api_service).keys()

    assert [v for v in variables if v.startswith("TALLY_MIGRATION_")] == []


def test_api_starts_after_the_data_stores_and_the_migrations(
    api_service: Service,
) -> None:
    assert _depends_on(api_service) == {
        "postgres": "service_healthy",
        "valkey": "service_healthy",
        "migrate": "service_completed_successfully",
    }


def test_postgres_runs_outside_the_profiles_on_the_tests_version(
    services: dict[str, Service],
) -> None:
    postgres = services["postgres"]

    assert "profiles" not in postgres
    assert postgres["image"] == POSTGRES_IMAGE


def test_postgres_port_is_published_on_localhost_only(
    services: dict[str, Service],
) -> None:
    assert _ports(services["postgres"]) == [("127.0.0.1", "5432", 5432)]


def test_postgres_health_check_uses_tcp(services: dict[str, Service]) -> None:
    healthcheck = cast("dict[str, object]", services["postgres"]["healthcheck"])
    test = cast("list[str]", healthcheck["test"])

    assert test[:2] == ["CMD", "pg_isready"]
    assert test[test.index("--host") + 1] == "127.0.0.1"


def test_postgres_runs_the_init_script(services: dict[str, Service]) -> None:
    volumes = cast("list[dict[str, object]]", services["postgres"]["volumes"])
    targets = {str(volume["target"]): volume for volume in volumes}

    init = targets["/docker-entrypoint-initdb.d/init.sql"]
    assert str(init["source"]).endswith("postgres-init.sql")
    assert init["read_only"] is True
    assert targets["/var/lib/postgresql"]["type"] == "volume"


def test_migrate_runs_the_migrations_once_postgres_is_healthy(
    services: dict[str, Service],
) -> None:
    migrate = services["migrate"]

    assert migrate["profiles"] == ["app"]
    assert migrate["command"] == ["alembic", "upgrade", "head"]
    assert _required_variables(MigrationSettings) <= _environment(migrate).keys()
    assert _depends_on(migrate) == {"postgres": "service_healthy"}


def test_valkey_runs_outside_the_profiles_on_the_tests_version(
    services: dict[str, Service],
) -> None:
    valkey = services["valkey"]

    assert "profiles" not in valkey
    assert valkey["image"] == VALKEY_IMAGE


def test_valkey_port_is_published_on_localhost_only(
    services: dict[str, Service],
) -> None:
    assert _ports(services["valkey"]) == [("127.0.0.1", "6379", 6379)]


def test_valkey_health_check_pings_it(services: dict[str, Service]) -> None:
    healthcheck = cast("dict[str, object]", services["valkey"]["healthcheck"])

    assert healthcheck["test"] == ["CMD", "valkey-cli", "ping"]


def test_valkey_never_evicts_keys(services: dict[str, Service]) -> None:
    command = cast("list[str]", services["valkey"]["command"])

    assert command[command.index("--maxmemory-policy") + 1] == "noeviction"


def test_the_worker_runs_only_in_the_app_profile(worker_service: Service) -> None:
    assert worker_service["profiles"] == ["app"]


def test_the_worker_runs_the_image_contracts_command(worker_service: Service) -> None:
    assert worker_service["command"] == worker_command(WORKER_MODULE)


def test_the_worker_serves_no_port_and_has_no_health_check(
    worker_service: Service,
) -> None:
    assert "ports" not in worker_service
    assert "healthcheck" not in worker_service


def test_the_worker_has_time_for_its_wait_before_it_is_killed(
    worker_service: Service,
) -> None:
    assert worker_service["stop_grace_period"] == "30s"


def test_the_worker_sets_every_required_setting_and_no_owner_role(
    worker_service: Service,
) -> None:
    variables = _environment(worker_service).keys()

    assert _required_variables(Settings) <= variables
    assert [v for v in variables if v.startswith("TALLY_MIGRATION_")] == []


def test_the_worker_starts_after_the_data_stores_and_the_migrations(
    worker_service: Service,
) -> None:
    assert _depends_on(worker_service) == {
        "postgres": "service_healthy",
        "valkey": "service_healthy",
        "migrate": "service_completed_successfully",
    }


def test_the_e2e_override_runs_the_test_worker_from_the_dev_stage() -> None:
    worker = _rendered("compose.yaml", "compose.e2e.yaml")["worker"]
    build = cast("dict[str, object]", worker["build"])

    assert build["target"] == "dev"
    assert worker["command"] == worker_command(TEST_WORKER_MODULE)
