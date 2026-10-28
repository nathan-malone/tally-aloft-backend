import subprocess
import sys
from typing import TYPE_CHECKING

from tests.support.paths import PYREFLY_TOML

if TYPE_CHECKING:
    from pathlib import Path

PROBE = """\
from tally_aloft.application.ports.health import DependencyCheck


class Probe:
    name = "probe"

    async def check(self{parameters}) -> None:
        return None


_check: DependencyCheck = Probe()
"""


def _pyrefly(tmp_path: Path, parameters: str) -> subprocess.CompletedProcess[str]:
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE.format(parameters=parameters))
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pyrefly",
            "check",
            "--config",
            str(PYREFLY_TOML),
            str(probe),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_fake_matching_its_port_passes(tmp_path: Path) -> None:
    result = _pyrefly(tmp_path, "")

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_fake_that_drifts_from_its_port_fails(tmp_path: Path) -> None:
    result = _pyrefly(tmp_path, ", timeout: float")

    assert result.returncode != 0
    assert "bad-assignment" in result.stdout + result.stderr
