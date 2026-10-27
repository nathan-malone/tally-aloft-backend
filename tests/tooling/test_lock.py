import re
import tomllib
from typing import cast

from tests.support.paths import ROOT, pyproject

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


def _lock() -> dict[str, object]:
    return tomllib.loads((ROOT / "uv.lock").read_text())


def _locked_names() -> set[str]:
    packages = cast("list[dict[str, object]]", _lock()["package"])
    return {str(package["name"]) for package in packages}


def test_resolution_takes_only_releases_at_least_7_days_old() -> None:
    uv = cast("dict[str, dict[str, object]]", pyproject()["tool"])["uv"]
    options = cast("dict[str, object]", _lock()["options"])

    assert uv["exclude-newer"] == "7 days"
    assert options["exclude-newer-span"] == "P7D"


def test_npm_resolution_takes_only_releases_at_least_7_days_old() -> None:
    workspace = (ROOT / "pnpm-workspace.yaml").read_text()

    setting = re.search(r"^minimumReleaseAge: 10080$", workspace, re.MULTILINE)

    assert setting is not None


def test_npm_tools_never_install_before_running() -> None:
    workspace = (ROOT / "pnpm-workspace.yaml").read_text()

    setting = re.search(r"^verifyDepsBeforeRun: error$", workspace, re.MULTILINE)

    assert setting is not None


def test_lock_pins_the_core_runtime_and_dev_tools() -> None:
    assert (RUNTIME | DEV) - _locked_names() == set()


def test_lock_pins_logfire_instrumentation_extras() -> None:
    assert LOGFIRE_EXTRAS - _locked_names() == set()


def test_npm_lock_pins_the_slop_checker() -> None:
    lock = (ROOT / "pnpm-lock.yaml").read_text()

    pin = re.search(r"^  aislop@[\d.]+:$", lock, re.MULTILINE)

    assert pin is not None
