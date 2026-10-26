import re

import pytest

from tests.support.paths import ROOT

WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
USES = re.compile(r"^\s*-?\s*uses:\s*(?P<action>\S+)", re.MULTILINE)
PINNED = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
RUN = re.compile(r"^\s*run:\s*(?P<command>.+)$", re.MULTILINE)
CHECKS = [
    "uv run ruff check",
    "uv run ruff format --check",
    "uv run pyrefly check",
    "uv run lint-imports",
    "uv run pytest",
    "docker build --platform linux/arm64",
]


def _workflow() -> str:
    return WORKFLOW.read_text()


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


def test_every_action_is_pinned_to_a_commit() -> None:
    actions = [match.group("action") for match in USES.finditer(_workflow())]

    assert actions != []
    assert [action for action in actions if PINNED.match(action) is None] == []


def test_checkout_keeps_no_credentials() -> None:
    workflow = _workflow()

    assert workflow.count("actions/checkout@") == workflow.count(
        "persist-credentials: false"
    )


@pytest.mark.parametrize("check", CHECKS)
def test_runs_every_check(check: str) -> None:
    commands = [match.group("command") for match in RUN.finditer(_workflow())]

    assert any(command.startswith(check) for command in commands)


def test_no_check_is_allowed_to_fail() -> None:
    workflow = _workflow()

    assert "continue-on-error" not in workflow
    assert "|| true" not in workflow
    assert re.search(r"^\s+if:", workflow, re.MULTILINE) is None


def test_every_install_is_locked() -> None:
    commands = [match.group("command") for match in RUN.finditer(_workflow())]
    syncs = [command for command in commands if command.startswith("uv sync")]

    assert syncs != []
    assert [sync for sync in syncs if "--locked" not in sync] == []
