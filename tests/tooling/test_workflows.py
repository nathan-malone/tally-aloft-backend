import re
from typing import TYPE_CHECKING

import pytest

from tests.support.workflows import PINNED, WORKFLOWS, actions

if TYPE_CHECKING:
    from pathlib import Path

# The rules every workflow follows, so a new workflow is covered without new
# tests. What only one workflow does is in its own test module.
FILES = sorted(WORKFLOWS.glob("*.yml"))


def test_both_workflows_are_covered() -> None:
    assert [path.name for path in FILES] == ["ci.yml", "release.yml"]


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.name)
def test_every_action_is_pinned_to_a_commit_and_its_release(path: Path) -> None:
    used = actions(path.read_text())

    assert used != []
    assert [action for action in used if PINNED.match(action) is None] == []


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.name)
def test_checkout_keeps_no_credentials(path: Path) -> None:
    workflow = path.read_text()

    assert workflow.count("actions/checkout@") == workflow.count(
        "persist-credentials: false"
    )


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.name)
def test_no_step_is_allowed_to_fail(path: Path) -> None:
    workflow = path.read_text()

    assert "continue-on-error" not in workflow
    assert "|| true" not in workflow
    assert re.search(r"^\s+if:", workflow, re.MULTILINE) is None


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.name)
def test_every_uv_command_asserts_the_lock(path: Path) -> None:
    locked = re.search(
        r'^env:\n(?:\s+#.*\n)*\s+UV_LOCKED: "1"\s*$', path.read_text(), re.MULTILINE
    )

    assert locked is not None
