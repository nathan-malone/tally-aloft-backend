import re

import pytest

from tests.support.workflows import (
    PINNED,
    actions,
    jobs,
    read_workflow,
    run_commands,
)

DOWNLOAD = "actions/download-artifact@"
UPLOAD = "actions/upload-artifact@"


def _workflow() -> str:
    return read_workflow("release.yml")


def _jobs() -> dict[str, str]:
    return jobs(_workflow())


def test_runs_on_pushed_version_tags_only() -> None:
    workflow = _workflow()

    trigger = re.search(
        r'^on:\n  push:\n    tags:\n    - "v\*"\n\n', workflow, re.MULTILINE
    )

    assert trigger is not None
    assert "pull_request" not in workflow
    assert "branches" not in workflow
    assert "workflow_dispatch" not in workflow


def test_has_exactly_the_verify_and_publish_jobs() -> None:
    assert list(_jobs()) == ["verify", "publish"]


def test_only_the_publish_job_can_write() -> None:
    workflow = _workflow()
    verify, publish = _jobs()["verify"], _jobs()["publish"]

    read = r"^    permissions:\n      contents: read$"
    write = r"^    permissions:\n      contents: write$"

    assert re.search(r"^permissions: \{\}$", workflow, re.MULTILINE) is not None
    assert re.search(read, verify, re.MULTILINE) is not None
    assert re.search(write, publish, re.MULTILINE) is not None
    assert len(re.findall(r":\s*write\b", workflow)) == 1


def test_publish_runs_after_verify_and_no_project_code() -> None:
    publish = _jobs()["publish"]

    assert re.search(r"^    needs: verify$", publish, re.MULTILINE) is not None
    assert [action.split("@")[0] + "@" for action in actions(publish)] == [DOWNLOAD]
    commands = run_commands(publish)
    assert commands != []
    assert all(command.startswith("gh release ") for command in commands)
    assert all(
        tool not in command.split()
        for command in commands
        for tool in ("make", "uv", "python")
    )


def test_publish_creates_the_release_then_attaches_the_schema() -> None:
    create, upload = run_commands(_jobs()["publish"])

    assert create.startswith('gh release view "$TAG" || gh release create "$TAG"')
    assert "--verify-tag" in create.split()
    assert upload == 'gh release upload "$TAG" openapi.json --clobber'


def test_verify_checks_the_schema_before_uploading_it() -> None:
    verify = _jobs()["verify"]

    assert run_commands(verify) == ["make openapi.check"]
    check = verify.index("run: make openapi.check")
    upload = verify.index(UPLOAD)
    assert check < upload
    assert "path: openapi.json" in verify[upload:]
    assert "if-no-files-found: error" in verify[upload:]


@pytest.mark.parametrize("job", ["verify", "publish"])
def test_every_job_has_a_timeout(job: str) -> None:
    timeout = re.search(r"^    timeout-minutes: \d+$", _jobs()[job], re.MULTILINE)

    assert timeout is not None


def test_no_command_interpolates_an_expression() -> None:
    commands = run_commands(_workflow())

    assert commands != []
    assert [command for command in commands if "${{" in command] == []


def test_every_action_is_pinned_to_a_commit_and_its_release() -> None:
    used = actions(_workflow())

    assert used != []
    assert [action for action in used if PINNED.match(action) is None] == []


def test_checkout_keeps_no_credentials() -> None:
    workflow = _workflow()

    assert workflow.count("actions/checkout@") == 1
    assert workflow.count("persist-credentials: false") == 1


def test_no_step_is_allowed_to_fail() -> None:
    workflow = _workflow()

    assert "continue-on-error" not in workflow
    assert "|| true" not in workflow
    assert re.search(r"^\s+if:", workflow, re.MULTILINE) is None


def test_every_uv_command_asserts_the_lock() -> None:
    assert re.search(r'^  UV_LOCKED: "1"$', _workflow(), re.MULTILINE) is not None
