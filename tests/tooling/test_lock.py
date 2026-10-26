import tomllib
from typing import cast

from tests.support.paths import ROOT

RUNTIME = {
    "alembic",
    "asyncpg",
    "fastapi",
    "logfire",
    "pydantic",
    "pydantic-settings",
    "redis",
    "sqlalchemy",
    "taskiq",
    "taskiq-redis",
    "uvicorn",
}
DEV = {
    "import-linter",
    "polyfactory",
    "pyrefly",
    "pytest",
    "pytest-asyncio",
    "ruff",
    "testcontainers",
}
LOGFIRE_EXTRAS = {
    "opentelemetry-instrumentation-asyncpg",
    "opentelemetry-instrumentation-fastapi",
    "opentelemetry-instrumentation-redis",
}


def _locked_names() -> set[str]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    packages = cast("list[dict[str, object]]", lock["package"])
    return {str(package["name"]) for package in packages}


def test_lock_pins_the_core_runtime_and_dev_tools() -> None:
    assert (RUNTIME | DEV) - _locked_names() == set()


def test_lock_pins_logfire_instrumentation_extras() -> None:
    assert LOGFIRE_EXTRAS - _locked_names() == set()
