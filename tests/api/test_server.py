import os
import subprocess

import httpx
import pytest

from tests.support.paths import ROOT
from tests.support.server import (
    API_APP,
    REQUIRED_SETTINGS,
    free_port,
    running_server,
    uvicorn_command,
    without_tally_variables,
)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("TALLY_FORWARDED_ALLOW_IPS", "*"),
        ("TALLY_CORS_ORIGINS", "*"),
        ("TALLY_DATABASE_URL", "not-a-url"),
        ("TALLY_DATABASE_URL", None),
    ],
    ids=["forwarded-allow-ips", "cors-origins", "database-url", "no-database-url"],
)
def test_invalid_settings_stop_the_api_before_it_serves(
    variable: str, value: str | None
) -> None:
    env = {**without_tally_variables(os.environ), **REQUIRED_SETTINGS}
    del env["TALLY_ENVIRONMENT"]
    if value is None:
        del env[variable]
    else:
        env[variable] = value

    result = subprocess.run(
        uvicorn_command(API_APP, free_port()),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode != 0
    assert result.stderr.count("TALLY_ENVIRONMENT") == 1
    assert result.stderr.count(variable) == 1
    assert "Traceback" not in result.stderr
    assert value is None or value not in result.stderr
    assert "Uvicorn running" not in result.stderr


def test_api_on_the_host_answers_livez_without_migration_settings() -> None:
    env = {**without_tally_variables(os.environ), **REQUIRED_SETTINGS}

    with running_server(API_APP, env) as base_url:
        response = httpx.get(f"{base_url}/livez")

    assert response.status_code == 200
