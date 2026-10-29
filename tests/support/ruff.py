import subprocess

from ruff import find_ruff_bin

from tests.support.paths import ROOT


def ruff(
    *arguments: str, source: str, filename: str
) -> subprocess.CompletedProcess[str]:
    """
    Run Ruff on `source` as though it were the repository file `filename`.

    The path decides which per-file rules apply. This runs the binary itself,
    not `python -m ruff`, which starts Python only to find it.
    """
    return subprocess.run(
        [find_ruff_bin(), *arguments, "--no-cache", "--stdin-filename", filename, "-"],
        input=source,
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
