import re

import pytest

from tests.support.makefile import targets
from tests.support.workflows import read_workflow, run_commands

# CI runs everything `make check` does, so adding a check to the Makefile
# without a CI step fails here. What each target runs is in test_makefile.py.
TARGETS = ["packages.install", "image.build", *targets()["check"]]


def _workflow() -> str:
    return read_workflow("ci.yml")


def _commands() -> list[str]:
    return run_commands(_workflow())


def test_runs_on_pull_requests_only() -> None:
    workflow = _workflow()

    trigger = re.search(r"^on:\s*\n\s+pull_request:\s*$", workflow, re.MULTILINE)

    assert trigger is not None
    assert "pull_request_target" not in workflow


def test_token_can_only_read_contents() -> None:
    workflow = _workflow()
    permissions = re.search(
        r"^permissions:\s*\n\s+contents: read\s*$", workflow, re.MULTILINE
    )

    assert permissions is not None
    assert re.search(r":\s*write\b", workflow) is None


@pytest.mark.parametrize("target", TARGETS)
def test_runs_every_target(target: str) -> None:
    assert any(command.split()[:2] == ["make", target] for command in _commands())


def test_runs_make_targets_only() -> None:
    commands = _commands()

    assert commands != []
    assert [command for command in commands if not command.startswith("make ")] == []


def test_builds_the_image_for_arm64() -> None:
    builds = [command for command in _commands() if "image.build" in command]

    assert builds != []
    assert all("PLATFORM=linux/arm64" in build.split() for build in builds)


def test_the_breaking_change_check_gets_the_read_only_token() -> None:
    workflow = _workflow()
    start = workflow.index("run: make openapi.breaking.check")
    # The step ends at the next step or the end of its job.
    ends = [workflow.find(end, start) for end in ("\n    - ", "\n\n")]
    step = workflow[start : min(end for end in ends if end != -1)]

    assert "GH_TOKEN: ${{ github.token }}" in step
    assert "GH_REPO: ${{ github.repository }}" in step
