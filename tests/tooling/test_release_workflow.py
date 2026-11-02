import re

import pytest

from tests.support.workflows import (
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

    # Only vX.Y.Z, the form supported-schema-releases.txt accepts.
    only_releases = re.escape('    - "v[0-9]+.[0-9]+.[0-9]+"')
    trigger = re.search(
        rf"^on:\n  push:\n    tags:\n{only_releases}\n\n", workflow, re.MULTILINE
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
    # Each command, split at the shell's if, then, else, fi, ;, &&, || and |.
    calls = [
        call
        for command in commands
        for call in re.split(r"^if |\s*(?:;\s*(?:then|else|fi)?|&&|\|\|?)\s*", command)
        if call != ""
    ]
    assert commands != []
    assert [call for call in calls if not call.startswith("gh release ")] == []


def test_publish_attaches_the_schema_to_a_new_or_existing_release() -> None:
    [publish] = run_commands(_jobs()["publish"])
    condition, existing, new = re.split(
        r"; then |; else ", publish.removesuffix("; fi")
    )

    assert condition == 'if gh release view "$TAG" > /dev/null'
    # An existing release, from a re-run, has its asset replaced, and a draft
    # an interrupted create left behind is published.
    assert existing == (
        'gh release upload "$TAG" openapi.json --clobber'
        ' && gh release edit "$TAG" --draft=false'
    )
    # A new release gets the asset as it is created, so it is never published
    # without one: gh creates it as a draft, uploads, then publishes.
    assert new == (
        'gh release create "$TAG" openapi.json --verify-tag --title "$TAG"'
        ' --notes "API schema for $TAG."'
    )


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


def test_publish_passes_the_token_repository_and_tag_through_env() -> None:
    publish = _jobs()["publish"]

    env = publish[publish.index("    env:\n") : publish.index("    steps:\n")]

    assert "      GH_TOKEN: ${{ github.token }}\n" in env
    assert "      GH_REPO: ${{ github.repository }}\n" in env
    assert "      TAG: ${{ github.ref_name }}\n" in env
