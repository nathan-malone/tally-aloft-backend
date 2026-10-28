import subprocess
import sys
import tempfile
from pathlib import Path

from tests.support.paths import PYREFLY_TOML


def pyrefly(*, source: str, filename: str) -> subprocess.CompletedProcess[str]:
    """
    Run Pyrefly on `source` as though it were the repository file `filename`.

    The path decides which sub-config applies, and sub-config paths are
    relative to the config, so the real config is copied beside the file.
    Project imports resolve through the virtual environment, so Pyrefly's
    warning that the copy's `src` search path doesn't exist is expected.
    """
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / PYREFLY_TOML.name
        _ = config.write_text(PYREFLY_TOML.read_text())
        file = Path(directory) / filename
        file.parent.mkdir(parents=True)
        _ = file.write_text(source)
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pyrefly",
                "check",
                "--config",
                str(config),
                str(file),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
