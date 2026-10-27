import re

import pytest

from tests.support.makefile import targets
from tests.support.paths import ROOT

WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
USES = re.compile(r"^\s*-?\s*uses:\s*(?P<action>\S+)", re.MULTILINE)
# An exact release, such as v7.0.1, never a major tag such as v7.
PINNED = re.compile(r"^[\w.-]+/[\w.-]+@v\d+\.\d+\.\d+$")
RUN = re.compile(r"^\s*run:\s*(?P<command>.+)$", re.MULTILINE)
# CI runs everything `make check` does, so adding a check to the Makefile
# without a CI step fails here. What each target runs is in test_makefile.py.
TARGETS = ["packages.install", "image.build", *targets()["check"]]


def _workflow() -> str:
    return WORKFLOW.read_text()


def _commands() -> list[str]:
    return [match.group("command") for match in RUN.finditer(_workflow())]


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


def test_every_action_is_pinned_to_an_exact_version() -> None:
    actions = [match.group("action") for match in USES.finditer(_workflow())]

    assert actions != []
    assert [action for action in actions if PINNED.match(action) is None] == []


def test_checkout_keeps_no_credentials() -> None:
    workflow = _workflow()

    assert workflow.count("actions/checkout@") == workflow.count(
        "persist-credentials: false"
    )


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


def test_no_check_is_allowed_to_fail() -> None:
    workflow = _workflow()

    assert "continue-on-error" not in workflow
    assert "|| true" not in workflow
    assert re.search(r"^\s+if:", workflow, re.MULTILINE) is None


def test_every_uv_command_asserts_the_lock() -> None:
    locked = re.search(
        r'^env:\n(?:\s+#.*\n)*\s+UV_LOCKED: "1"\s*$', _workflow(), re.MULTILINE
    )

    assert locked is not None
