"""Read GitHub Actions workflows as text, for the tooling tests."""

import re

from tests.support.paths import ROOT

WORKFLOWS = ROOT / ".github" / "workflows"
USES = re.compile(r"^\s*-?\s*uses:\s*(?P<action>.+?)\s*$", re.MULTILINE)
# A full commit SHA, which can't be moved as a tag can, then its exact release,
# such as `# v7.0.1`, never a major tag such as v7.
PINNED = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")
RUN = re.compile(r"^\s*run:\s*(?P<command>.+)$", re.MULTILINE)
# A job's key, indented two spaces under `jobs:`.
_JOB = re.compile(r"^  (?P<name>[\w-]+):\s*$", re.MULTILINE)


def read_workflow(name: str) -> str:
    """Return the text of `.github/workflows/<name>`."""
    return (WORKFLOWS / name).read_text()


def actions(text: str) -> list[str]:
    """Return every action a workflow's `uses:` lines name."""
    return [match.group("action") for match in USES.finditer(text)]


def run_commands(text: str) -> list[str]:
    """Return every single-line `run:` command."""
    return [match.group("command") for match in RUN.finditer(text)]


def jobs(text: str) -> dict[str, str]:
    """Return each job's name and text, from a workflow's `jobs:` block."""
    _, _, block = text.partition("\njobs:\n")
    starts = list(_JOB.finditer(block))
    ends = [match.start() for match in starts[1:]] + [len(block)]
    return {
        match.group("name"): block[match.start() : end]
        for match, end in zip(starts, ends, strict=True)
    }
