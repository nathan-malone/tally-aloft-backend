import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import NoReturn

import pytest
from scripts import check_breaking_changes as script
from scripts.check_breaking_changes import (
    OASDIFF_IMAGE,
    check,
    gh_fetch,
    main,
    read_releases,
    run_oasdiff,
)

from tests.support.paths import ROOT

# A tool image is pinned to its release and its multi-architecture digest
# (tally-aloft-docs/technical/supply-chain.md).
PINNED_IMAGE = re.compile(r"^[\w./-]+:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}$")


def _schema(
    operations: dict[str, str],
    fields: tuple[str, ...] = (),
    deprecated: tuple[str, ...] = (),
) -> bytes:
    """
    Return a schema with one GET per path, each answering an object.

    The object has a required `id` and an optional string for each of `fields`.
    The operations on the `deprecated` paths are marked deprecated.
    """
    properties = {"id": {"type": "string"}} | {
        name: {"type": "string"} for name in fields
    }
    response = {
        "description": "OK",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": properties,
                    "required": ["id"],
                }
            }
        },
    }
    paths = {
        path: {
            "get": {
                "operationId": operation_id,
                "deprecated": path in deprecated,
                "responses": {"200": response},
            }
        }
        for path, operation_id in operations.items()
    }
    document = {
        "openapi": "3.1.0",
        "info": {"title": "Tally Aloft API", "version": "0.1.0"},
        "paths": paths,
    }
    return json.dumps(document).encode()


FLIGHTS = {"/flights": "logbook_list_flights"}
BOTH = {"/flights": "logbook_list_flights", "/aircraft": "logbook_list_aircraft"}


def _releases(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "supported-schema-releases.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _current(tmp_path: Path, schema: bytes) -> Path:
    path = tmp_path / "openapi.json"
    path.write_bytes(schema)
    return path


def _gh_on_path(_name: str) -> str:
    return "/usr/bin/gh"


def _docker_on_path(_name: str) -> str:
    return "/usr/bin/docker"


def _nothing_on_path(_name: str) -> None:
    return None


def _unused_fetch(tag: str) -> NoReturn:
    pytest.fail(f"fetched {tag}")


def _compatible(_base: bytes, _current: Path) -> int:
    return 0


def _unused_compare(_base: bytes, _current: Path) -> NoReturn:
    pytest.fail("ran oasdiff")


def test_a_list_of_comments_passes_without_fetching(tmp_path: Path) -> None:
    releases = _releases(tmp_path, "# Only comments.\n\n   # And blanks.\n")

    code = check(releases, _current(tmp_path, b"{}"), _unused_fetch, _unused_compare)

    assert code == 0


def test_tags_may_carry_comments(tmp_path: Path) -> None:
    releases = _releases(tmp_path, "v1.2.3  # iOS 1.4\nv1.10.0\n")

    assert read_releases(releases) == ["v1.2.3", "v1.10.0"]


@pytest.mark.parametrize("line", ["latest", "--repo=x/y", "1.2.3", "v1.2", "v1.2.3 x"])
def test_a_malformed_line_fails_naming_it_without_fetching(
    tmp_path: Path, line: str, capsys: pytest.CaptureFixture[str]
) -> None:
    releases = _releases(tmp_path, f"# Header.\n{line}\n")

    code = check(releases, _current(tmp_path, b"{}"), _unused_fetch, _unused_compare)

    assert code == 1
    assert "line 2" in capsys.readouterr().err


def test_a_missing_asset_fails_naming_the_tag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    releases = _releases(tmp_path, "v0.1.0\n")

    code = check(releases, _current(tmp_path, b"{}"), lambda _tag: None)

    assert code == 1
    err = capsys.readouterr().err
    assert "v0.1.0" in err
    assert "Supported schema releases" in err


def test_a_compatible_release_passes_silently(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    releases = _releases(tmp_path, "v0.1.0\n")

    code = check(releases, _current(tmp_path, b"{}"), lambda _tag: b"{}", _compatible)

    assert code == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("compared", "reported"),
    [
        (1, "v0.1.0: breaking changes"),
        (102, "v0.1.0: oasdiff failed with exit code 102"),
    ],
)
def test_an_oasdiff_failure_is_reported_with_the_tag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], compared: int, reported: str
) -> None:
    releases = _releases(tmp_path, "v0.1.0\n")

    def compare(_base: bytes, _current: Path) -> int:
        return compared

    code = check(releases, _current(tmp_path, b"{}"), lambda _tag: b"{}", compare)

    assert code == 1
    assert reported in capsys.readouterr().err


def test_every_tag_is_checked_before_failing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    releases = _releases(tmp_path, "v0.1.0\nv0.2.0\nv0.3.0\n")
    assets = {"v0.1.0": b"breaking", "v0.3.0": b"fine"}
    compared: list[bytes] = []

    def compare(base: bytes, _current: Path) -> int:
        compared.append(base)
        return 1 if base == b"breaking" else 0

    code = check(releases, _current(tmp_path, b"{}"), assets.get, compare)

    assert code == 1
    assert compared == [b"breaking", b"fine"]
    err = capsys.readouterr().err
    assert "v0.1.0: breaking changes" in err
    assert "v0.2.0: no openapi.json release asset" in err
    assert "v0.3.0" not in err


def test_gh_fetch_downloads_the_release_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check is False
        calls.append(command)
        directory = command[command.index("--dir") + 1]
        (Path(directory) / "openapi.json").write_bytes(b"schema")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(shutil, "which", _gh_on_path)
    monkeypatch.setattr(subprocess, "run", run)

    assert gh_fetch("v1.2.3") == b"schema"
    [command] = calls
    assert command[:-1] == [
        "/usr/bin/gh",
        "release",
        "download",
        "v1.2.3",
        "--pattern",
        "openapi.json",
        "--dir",
    ]


def test_gh_fetch_returns_nothing_when_gh_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check is False
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(shutil, "which", _gh_on_path)
    monkeypatch.setattr(subprocess, "run", run)

    assert gh_fetch("v1.2.3") is None


def test_gh_fetch_needs_gh(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(shutil, "which", _nothing_on_path)

    assert gh_fetch("v1.2.3") is None
    assert "(gh)" in capsys.readouterr().err


def test_oasdiff_needs_docker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(shutil, "which", _nothing_on_path)

    assert run_oasdiff(b"{}", _current(tmp_path, b"{}")) == script.NOT_INSTALLED
    assert "Docker" in capsys.readouterr().err


def test_oasdiff_runs_isolated_with_the_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    levels: list[str] = []

    def run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check is False
        calls.append(command)
        mount = command[command.index("--volume") + 1]
        directory = mount.removesuffix(":/specs:ro")
        levels.append((Path(directory) / "levels.txt").read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(shutil, "which", _docker_on_path)
    monkeypatch.setattr(subprocess, "run", run)

    assert run_oasdiff(b"{}", _current(tmp_path, b"{}")) == 0
    [command] = calls
    assert command[:6] == [
        "/usr/bin/docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--volume",
    ]
    assert command[6].endswith(":/specs:ro")
    assert command[7:] == [
        OASDIFF_IMAGE,
        "breaking",
        "/specs/base.json",
        "/specs/current.json",
        "--fail-on",
        "WARN",
        "--severity-levels",
        "/specs/levels.txt",
        "--allow-external-refs=false",
        "--color",
        "never",
    ]
    assert levels == ["".join(f"{rule} err\n" for rule in script.SEVERITY_OVERRIDES)]


def test_main_passes_on_a_list_of_comments(tmp_path: Path) -> None:
    releases = _releases(tmp_path, "# Nothing supported yet.\n")
    current = _current(tmp_path, b"{}")

    assert main(["--releases", str(releases), "--current", str(current)]) == 0


def test_the_committed_list_parses() -> None:
    read_releases(ROOT / "supported-schema-releases.txt")


def test_the_oasdiff_image_is_pinned_to_a_release_and_digest() -> None:
    assert PINNED_IMAGE.match(OASDIFF_IMAGE) is not None


@pytest.mark.container
@pytest.mark.parametrize(
    ("base", "current", "code"),
    [
        (_schema(BOTH), _schema(FLIGHTS), script.BREAKING),
        (_schema(FLIGHTS), _schema(FLIGHTS, ("note",)), 0),
        (
            _schema(FLIGHTS),
            _schema({"/flights": "logbook_get_flights"}),
            script.BREAKING,
        ),
        (_schema(FLIGHTS, ("note",)), _schema(FLIGHTS), script.BREAKING),
        (_schema(BOTH, deprecated=("/aircraft",)), _schema(FLIGHTS), script.BREAKING),
        (_schema(FLIGHTS), _schema(FLIGHTS), 0),
    ],
    ids=[
        "removed-operation",
        "added-optional-field",
        "renamed-operation-id",
        "removed-optional-field",
        "removed-deprecated-operation",
        "unchanged",
    ],
)
def test_oasdiff_judges_each_change(
    tmp_path: Path, base: bytes, current: bytes, code: int
) -> None:
    assert run_oasdiff(base, _current(tmp_path, current)) == code
