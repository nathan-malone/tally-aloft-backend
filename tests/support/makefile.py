import os
import re
import shutil
import subprocess

import pytest

from tests.support.paths import ROOT

MAKEFILE = ROOT / "Makefile"
_RULE = re.compile(r"^(?P<target>[a-z][\w.]*):(?P<prerequisites>.*)$", re.MULTILINE)
# Under `make test`, the outer make's flags and variables, such as ARGS, would
# otherwise reach a make that a test runs, through the environment.
_MAKE_VARIABLES = {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES"}


def targets() -> dict[str, list[str]]:
    """Return each target the Makefile defines, with its prerequisites."""
    return {
        match.group("target"): match.group("prerequisites").split()
        for match in _RULE.finditer(MAKEFILE.read_text())
    }


def make(*arguments: str) -> list[str]:
    """Run make in the repository root and return its output lines."""
    path = shutil.which("make")
    if path is None:
        pytest.fail("this test needs GNU Make on PATH")
    env = {
        name: value for name, value in os.environ.items() if name not in _MAKE_VARIABLES
    }
    result = subprocess.run(
        [path, "--no-print-directory", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines()]


def dry_run(*arguments: str) -> list[str]:
    """Return the commands make would run, without `==>` banners."""
    lines = make("--dry-run", *arguments)
    return [line for line in lines if not line.startswith("==>")]
