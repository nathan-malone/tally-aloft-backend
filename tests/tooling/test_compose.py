import json
import subprocess
from typing import cast

import pytest

from tally_aloft.bootstrap.settings import MigrationSettings, Settings, variable_name
from tests.support.containers import docker_cli
from tests.support.paths import ROOT
from tests.support.postgres import POSTGRES_IMAGE

pytestmark = pytest.mark.container

Service = dict[str, object]


@pytest.fixture(scope="module")
def services() -> dict[str, Service]:
    """Return every service as Compose renders it under profile `app`."""
    result = subprocess.run(
        [docker_cli(), "compose", "--profile", "app", "config", "--format", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return cast("dict[str, Service]", json.loads(result.stdout)["services"])


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


def test_api_starts_after_the_migrations(api_service: Service) -> None:
    assert _depends_on(api_service) == {
        "postgres": "service_healthy",
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
