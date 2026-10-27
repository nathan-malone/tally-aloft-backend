import re

import pytest

from tests.support.makefile import MAKEFILE, dry_run, make, targets

# Recipes run under cmd.exe as well as sh, so they hold plain commands only
# (tally-aloft-docs/technical/make-commands.md, "Rules").
SHELL_SYNTAX = re.compile(r"[|&;<>'`*^%]|\$\$")
AGGREGATES = [("check", "test"), ("check.fast", "test.fast")]


def _makefile() -> str:
    return MAKEFILE.read_text()


def _phony() -> set[str]:
    lines = re.findall(r"^\.PHONY:(.*)$", _makefile(), re.MULTILINE)
    return {target for line in lines for target in line.split()}


def test_commands_are_not_echoed() -> None:
    output = make("test.fast", "ARGS=--version")

    assert "==> test.fast" in output
    assert [line for line in output if line.startswith("uv ")] == []


def test_make_alone_prints_help() -> None:
    assert make()[0].startswith("Usage: make <target>")


def test_every_target_is_phony_and_in_help() -> None:
    help_lines = make("help")

    assert set(targets()) == _phony()
    assert [
        target
        for target in targets()
        if target != "help"
        and not any(line.split()[:1] == [target] for line in help_lines)
    ] == []


def test_recipes_use_no_shell_syntax() -> None:
    recipes = [
        line.strip()
        for line in _makefile().splitlines()
        if line.startswith("\t") and not line.strip().startswith("$(")
    ]
    shell_syntax = [
        recipe for recipe in recipes if SHELL_SYNTAX.search(recipe) is not None
    ]

    assert recipes != []
    assert shell_syntax == [], "recipes must run unchanged under cmd.exe and sh"


@pytest.mark.parametrize(("aggregate", "tests"), AGGREGATES)
def test_aggregates_run_every_check_then_their_tests(
    aggregate: str, tests: str
) -> None:
    *checks, last = targets()[aggregate]
    every_check = {target for target in targets() if target.endswith(".check")}

    assert set(checks) == every_check
    assert last == tests


@pytest.mark.parametrize("aggregate", [aggregate for aggregate, _ in AGGREGATES])
def test_aggregates_run_their_targets_in_order(aggregate: str) -> None:
    parts = [command for target in targets()[aggregate] for command in dry_run(target)]

    assert dry_run(aggregate) == parts


def test_test_fast_skips_only_the_container_tests() -> None:
    assert dry_run("test.fast") == [
        dry_run("test")[0].replace("pytest", 'pytest -m "not container"')
    ]


def test_test_passes_arguments_to_pytest() -> None:
    assert dry_run("test", "ARGS=-k settings")[0].endswith("pytest -k settings")


def test_lint_fix_formats_before_its_final_check() -> None:
    fix = dry_run("lint.fix")

    assert any(command.endswith("ruff format") for command in fix[:-1])
    assert fix[-1] == dry_run("lint.check")[0]


def _keeps_the_lock(command: str) -> bool:
    tool, subcommand, *_ = command.split()
    if tool == "uv":
        return "--locked" in command
    return subcommand in {"exec", "outdated"} or "--frozen-lockfile" in command


def test_only_the_lock_targets_may_rewrite_the_lock() -> None:
    commands = [
        command
        for target in targets()
        if target not in {"help", "packages.lock", "packages.update"}
        for command in dry_run(target)
        if command.startswith(("uv ", "pnpm "))
    ]

    assert {command.split()[0] for command in commands} == {"uv", "pnpm"}
    assert [command for command in commands if not _keeps_the_lock(command)] == []


@pytest.mark.parametrize(
    ("package", "upgrade"), [("", "--upgrade"), ("ruff", "--upgrade-package ruff")]
)
def test_update_upgrades_then_installs(package: str, upgrade: str) -> None:
    update = dry_run("packages.update", f"PACKAGE={package}")

    assert update[0].endswith(f"lock {upgrade}")
    assert update[1] == f"pnpm update {package}".strip()
    assert update[2:] == dry_run("packages.install")


def test_image_build_targets_the_platform_only_when_given() -> None:
    [cross] = dry_run("image.build", "IMAGE=backend:ci", "PLATFORM=linux/arm64")
    [native] = dry_run("image.build", "IMAGE=backend:ci")

    assert "--platform linux/arm64 -t backend:ci" in cross
    assert "--platform" not in native
