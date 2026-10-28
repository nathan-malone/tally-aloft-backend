import subprocess
import tempfile
from pathlib import Path

from pyrefly.__main__ import get_pyrefly_bin

from tests.support.paths import PYREFLY_TOML


def pyrefly(*, source: str, filename: str) -> subprocess.CompletedProcess[str]:
    """
    Run Pyrefly on `source` as though it were the repository file `filename`.

    The path decides which sub-config applies, and sub-config paths are
    relative to the config, so the real config is copied beside the file.
    Project imports resolve through the virtual environment, so Pyrefly's
    warning that the copy's `src` search path doesn't exist is expected.

    This runs the binary itself: `python -m pyrefly` starts it as a child,
    which the timeout wouldn't kill, and on Windows `subprocess.run` then
    waits for it to close the output pipes.
    """
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / PYREFLY_TOML.name
        _ = config.write_text(PYREFLY_TOML.read_text())
        file = Path(directory) / filename
        file.parent.mkdir(parents=True)
        _ = file.write_text(source)
        return subprocess.run(
            [get_pyrefly_bin(), "check", "--config", str(config), str(file)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
