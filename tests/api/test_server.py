import os
import subprocess

import httpx
import pytest

from tests.support.paths import ROOT
from tests.support.server import (
    API_APP,
    free_port,
    running_server,
    uvicorn_command,
    without_tally_variables,
)


@pytest.mark.parametrize(
    "variable", ["TALLY_FORWARDED_ALLOW_IPS", "TALLY_CORS_ORIGINS"]
)
def test_invalid_settings_stop_the_api_before_it_serves(variable: str) -> None:
    env = {**without_tally_variables(os.environ), variable: "*"}

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
    assert "*" not in result.stderr
    assert "Uvicorn running" not in result.stderr


def test_api_on_the_host_answers_livez() -> None:
    env = {**without_tally_variables(os.environ), "TALLY_ENVIRONMENT": "development"}

    with running_server(API_APP, env) as base_url:
        response = httpx.get(f"{base_url}/livez")

    assert response.status_code == 200
