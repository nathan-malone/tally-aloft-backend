from typing import TYPE_CHECKING, cast

from tests.support.paths import RUFF_TOML, read_toml
from tests.support.ruff import ruff

if TYPE_CHECKING:
    import subprocess


def _ruff_config() -> dict[str, object]:
    return read_toml(RUFF_TOML)


def _lint_config() -> dict[str, object]:
    return cast("dict[str, object]", _ruff_config()["lint"])


def _ignored_rules() -> list[str]:
    """Return every rule in the global and per-file ignore lists."""
    lint = _lint_config()
    per_file = cast("dict[str, list[str]]", lint.get("per-file-ignores", {}))
    return [
        *cast("list[str]", lint["ignore"]),
        *(rule for rules in per_file.values() for rule in rules),
    ]


def _ruff_lint_lines() -> list[str]:
    """Return the raw lines of every `[lint…]` table."""
    lines: list[str] = []
    in_ruff_lint = False
    for line in RUFF_TOML.read_text().splitlines():
        if line.startswith("["):
            in_ruff_lint = line.startswith("[lint")
        elif in_ruff_lint:
            lines.append(line)
    return lines


def _ruff_check(source: str, filename: str) -> subprocess.CompletedProcess[str]:
    return ruff("check", "--output-format", "concise", source=source, filename=filename)


def test_selects_every_rule_without_preview() -> None:
    ruff = _ruff_config()
    lint = _lint_config()

    assert lint["select"] == ["ALL"]
    assert ruff["line-length"] == 88
    assert ruff.get("preview", False) is False
    assert lint.get("preview", False) is False


def test_global_ignores_are_few_and_follow_the_docstring_policy() -> None:
    ignore = cast("list[str]", _lint_config()["ignore"])

    assert len(ignore) <= 10
    assert {"D100", "D104", "D203", "D212"} <= set(ignore)
    assert {"D213", "D211"}.isdisjoint(ignore)


def test_every_ignore_has_a_reason() -> None:
    lines = _ruff_lint_lines()
    missing = [
        rule
        for rule in _ignored_rules()
        if not any(f'"{rule}"' in line and "#" in line for line in lines)
    ]

    assert missing == []


def _source(*lines: str) -> str:
    return "\n".join(lines) + "\n"


UNDOCUMENTED = _source("def probe() -> int:", "    return 1")
DOCUMENTED = _source(
    "def probe() -> int:",
    '    """',
    "    Return one.",
    "",
    "    More.",
    '    """',
    "    return 1",
)
SUMMARY_ON_OPENING_LINE = _source(
    "def probe() -> int:",
    '    """Return one.',
    "",
    "    More.",
    '    """',
    "    return 1",
)
TEST_FUNCTION = _source("def test_probe() -> None:", "    assert len([1]) == 1")


def test_public_function_in_src_needs_a_docstring() -> None:
    result = _ruff_check(UNDOCUMENTED, "src/tally_aloft/probe.py")

    assert result.returncode == 1
    assert "D103" in result.stdout


def test_documented_function_in_src_passes() -> None:
    result = _ruff_check(DOCUMENTED, "src/tally_aloft/probe.py")

    assert result.returncode == 0, result.stdout


def test_test_function_needs_no_docstring() -> None:
    result = _ruff_check(TEST_FUNCTION, "tests/unit/test_probe.py")

    assert result.returncode == 0, result.stdout


def test_multi_line_summary_on_the_opening_line_fails() -> None:
    result = _ruff_check(SUMMARY_ON_OPENING_LINE, "src/tally_aloft/probe.py")

    assert result.returncode == 1
    assert "D213" in result.stdout
