import json
import os
import runpy
import socket
import sys
from typing import TYPE_CHECKING, NoReturn

import pytest

from tests.support.paths import ROOT

if TYPE_CHECKING:
    from pathlib import Path

SCRIPT = ROOT / "scripts" / "export_openapi.py"
COMMITTED = ROOT / "openapi.json"


def _run(monkeypatch: pytest.MonkeyPatch, *arguments: str) -> int | str | None:
    """Run the script as `python scripts/export_openapi.py` would, in process."""
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), *arguments])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    return exit_info.value.code


def test_export_matches_the_committed_file_without_connecting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        msg = "the export opened a socket"
        raise AssertionError(msg)

    for name in [name for name in os.environ if name.startswith("TALLY_")]:
        monkeypatch.delenv(name)
    monkeypatch.setattr(socket, "socket", refuse)
    output = tmp_path / "openapi.json"

    code = _run(monkeypatch, "--output", str(output))

    assert code == 0
    assert output.read_bytes() == COMMITTED.read_bytes()
    assert json.loads(output.read_bytes())["paths"] == {}


def test_check_fails_on_a_stale_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stale = tmp_path / "openapi.json"
    stale.write_text("{}\n")

    code = _run(monkeypatch, "--check", "--output", str(stale))

    assert code == 1
    assert "make openapi.fix" in capsys.readouterr().err
    assert stale.read_text() == "{}\n"


def test_check_fails_on_a_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _run(monkeypatch, "--check", "--output", str(tmp_path / "none")) == 1


def test_check_passes_on_a_current_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    current = tmp_path / "openapi.json"
    current.write_bytes(COMMITTED.read_bytes())

    assert _run(monkeypatch, "--check", "--output", str(current)) == 0
