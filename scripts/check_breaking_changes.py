"""
Check `openapi.json` for breaking changes against each supported release.

`make openapi.breaking.check` runs it. For each tag in
`supported-schema-releases.txt`, it fetches that release's `openapi.json` asset
with `gh` and compares the current file with it using oasdiff's pinned image.
An empty list passes without fetching or running anything.
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RELEASES = ROOT / "supported-schema-releases.txt"
DEFAULT_CURRENT = ROOT / "openapi.json"
ASSET = "openapi.json"
# v1.34.1's multi-architecture index: the tag keeps it readable, and Docker
# runs the digest. Update both together, to a release at least 7 days old.
OASDIFF_IMAGE = (
    "tufin/oasdiff:v1.34.1"
    "@sha256:67a23cfea7d251e2ae34ee703ac8733c5d96bc2e7b90fd0f4d475ec99367b85f"
)
# oasdiff ranks these INFO, which `breaking` neither reports nor fails on, but
# the client contract calls each one breaking.
SEVERITY_OVERRIDES = {
    # A renamed operation ID renames the generated client function.
    "api-operation-id-removed": "err",
    # An app may read the field it would no longer get.
    "response-optional-property-removed": "err",
    # Deprecation doesn't make a removal safe while a listed client uses it.
    "api-path-removed-with-deprecation": "err",
    "api-removed-with-deprecation": "err",
}
BREAKING = 1
# The conventional exit code of a command that isn't installed.
NOT_INSTALLED = 127
_TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
_FIX_HINT = 'see "Supported schema releases" in the README'

type Fetch = Callable[[str], bytes | None]
type Compare = Callable[[bytes, Path], int]


class ReleaseListError(Exception):
    """A line of the supported-releases list isn't a release tag."""


def _report(message: str) -> None:
    print(message, file=sys.stderr)  # noqa: T201  # A CLI reports on stderr.


def read_releases(path: Path) -> list[str]:
    """Return the tags in the list at `path`, without comments and blank lines."""
    tags: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = line.partition("#")[0].strip()
        if text == "":
            continue
        if _TAG.fullmatch(text) is None:
            msg = f"{path.name}, line {number}: not a vX.Y.Z release tag"
            raise ReleaseListError(msg)
        tags.append(text)
    return tags


def gh_fetch(tag: str) -> bytes | None:
    """
    Download `tag`'s `openapi.json` release asset with `gh`, or return `None`.

    `gh` reads `GH_TOKEN` and `GH_REPO` from the environment, and prints why a
    download failed.
    """
    gh = shutil.which("gh")
    if gh is None:
        _report("The GitHub CLI (gh) is needed to fetch release schemas.")
        return None
    with tempfile.TemporaryDirectory() as directory:
        result = subprocess.run(  # noqa: S603  # Fixed arguments; the tag is validated.
            [gh, "release", "download", tag, "--pattern", ASSET, "--dir", directory],
            check=False,
        )
        asset = Path(directory) / ASSET
        if result.returncode != 0 or not asset.is_file():
            return None
        return asset.read_bytes()


def run_oasdiff(base: bytes, current: Path) -> int:
    """
    Compare `current` with the `base` schema in oasdiff, returning its exit code.

    oasdiff runs without a network and without external refs, so no schema can
    make it fetch anything.
    """
    docker = shutil.which("docker")
    if docker is None:
        _report("Docker is needed to run oasdiff.")
        return NOT_INSTALLED
    with tempfile.TemporaryDirectory() as directory:
        specs = Path(directory)
        _ = (specs / "base.json").write_bytes(base)
        _ = (specs / "current.json").write_bytes(current.read_bytes())
        levels = "".join(
            f"{rule} {level}\n" for rule, level in SEVERITY_OVERRIDES.items()
        )
        _ = (specs / "levels.txt").write_text(levels, encoding="utf-8")
        command = [
            docker,
            "run",
            "--rm",
            "--network",
            "none",
            "--volume",
            f"{specs}:/specs:ro",
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
        return subprocess.run(command, check=False).returncode  # noqa: S603  # Fixed arguments.


def check(
    releases: Path, current: Path, fetch: Fetch, compare: Compare = run_oasdiff
) -> int:
    """Check `current` against every listed release, returning the exit code."""
    try:
        tags = read_releases(releases)
    except ReleaseListError as error:
        _report(str(error))
        return 1
    # Every tag is checked, so one run reports every release it breaks.
    compatible = [_check_release(tag, current, fetch, compare) for tag in tags]
    return 0 if all(compatible) else 1


def _check_release(tag: str, current: Path, fetch: Fetch, compare: Compare) -> bool:
    """Report on one release, returning whether `current` is compatible with it."""
    base = fetch(tag)
    if base is None:
        _report(f"{tag}: no {ASSET} release asset could be fetched; {_FIX_HINT}.")
        return False
    code = compare(base, current)
    if code == BREAKING:
        _report(f"{tag}: breaking changes (above).")
    elif code != 0:
        _report(f"{tag}: oasdiff failed with exit code {code}.")
    return code == 0


def main(argv: Sequence[str] | None = None) -> int:
    """Check the schema against the supported releases, returning the exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--releases", type=Path, default=DEFAULT_RELEASES)
    _ = parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    arguments = parser.parse_args(argv)
    return check(arguments.releases, arguments.current, gh_fetch)


if __name__ == "__main__":
    sys.exit(main())
