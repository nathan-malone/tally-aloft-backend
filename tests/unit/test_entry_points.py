"""The entry points configure telemetry as the processes start."""

import json
import os
import subprocess
import sys

from tally_aloft.bootstrap.settings import (
    FORBIDDEN_VARIABLES,
    UNUSED_DATABASE_URL,
    UNUSED_VALKEY_URL,
)

API_PROBE = """
import json
import opentelemetry.trace
import tally_aloft.main as main
import logfire
print(json.dumps({
    "instrumented": getattr(main.app, "_is_instrumented_by_opentelemetry", False),
    "fastapi_tracing": main.app._telemetry["tracing"],
    "provider": type(opentelemetry.trace.get_tracer_provider()).__module__,
    "service": logfire.DEFAULT_LOGFIRE_INSTANCE.config.service_name,
}))
"""
WORKER_PROBE = """
import json
import tally_aloft.worker
import logfire
print(json.dumps({
    "service": logfire.DEFAULT_LOGFIRE_INSTANCE.config.service_name,
    "send": logfire.DEFAULT_LOGFIRE_INSTANCE.config.send_to_logfire,
}))
"""


def probe(code: str) -> dict[str, object]:
    """Run `code` in a fresh interpreter with the minimal settings; return its JSON."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("TALLY_", "LOGFIRE_"))
        and name not in FORBIDDEN_VARIABLES
    }
    environment |= {
        "TALLY_ENVIRONMENT": "test",
        "TALLY_DATABASE_URL": UNUSED_DATABASE_URL,
        "TALLY_VALKEY_URL": UNUSED_VALKEY_URL,
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    output: object = json.loads(result.stdout.splitlines()[-1])
    assert isinstance(output, dict)
    return output


def test_the_api_module_traces_its_app() -> None:
    assert probe(API_PROBE) == {
        "instrumented": True,
        "fastapi_tracing": False,
        "provider": "logfire._internal.tracer",
        "service": "tally-aloft-api",
    }


def test_the_worker_module_configures_telemetry() -> None:
    assert probe(WORKER_PROBE) == {"service": "tally-aloft-worker", "send": False}
