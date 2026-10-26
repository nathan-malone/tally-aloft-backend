import os
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from tests.tooling.paths import PYPROJECT, ROOT

if TYPE_CHECKING:
    from pathlib import Path

RUN_LINT_IMPORTS = (
    "import sys\n"
    "from importlinter.cli import lint_imports\n"
    "sys.exit(lint_imports(config_filename=sys.argv[1], no_cache=True))\n"
)


@pytest.fixture
def package(tmp_path: Path) -> Path:
    """Copy the source tree, so a test can add a violation to the copy."""
    shutil.copytree(
        ROOT / "src" / "tally_aloft",
        tmp_path / "tally_aloft",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return tmp_path / "tally_aloft"


def _lint_imports(package: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(package.parent)}
    return subprocess.run(
        [sys.executable, "-c", RUN_LINT_IMPORTS, str(PYPROJECT)],
        capture_output=True,
        text=True,
        cwd=package.parent,
        env=env,
        check=False,
    )


def test_the_unchanged_tree_keeps_every_contract(package: Path) -> None:
    result = _lint_imports(package)

    assert result.returncode == 0, result.stdout


def test_domain_importing_a_framework_breaks_a_contract(package: Path) -> None:
    (package / "domain" / "shared" / "_probe.py").write_text("import fastapi\n")

    result = _lint_imports(package)

    assert result.returncode != 0
    assert "Domain and application have no framework or vendor code BROKEN" in (
        result.stdout
    )


def test_inbound_adapter_importing_outbound_breaks_a_contract(package: Path) -> None:
    outbound = package / "adapters" / "outbound" / "probe"
    outbound.mkdir()
    (outbound / "__init__.py").write_text("")
    (package / "adapters" / "inbound" / "http" / "_probe.py").write_text(
        "import tally_aloft.adapters.outbound.probe\n"
    )

    result = _lint_imports(package)

    assert result.returncode != 0
    assert "Adapters are independent BROKEN" in result.stdout
