import re
from pathlib import Path

from tests.support.paths import PYREFLY_TOML, ROOT, read_toml

# Built in two parts so this file's own pattern isn't read as a suppression.
SUPPRESSION = "pyrefly" + ": ignore"
WELL_FORMED = re.compile(re.escape(SUPPRESSION) + r"\[[a-z-]+(, [a-z-]+)*\]\s+#\s*\S")
TURNED_OFF = re.compile(r'=\s*(false|"ignore"|"warn")')
ERROR_TABLES = {"[errors]", "[sub-config.errors]"}
MAX_SRC_SUPPRESSIONS = 10


def _suppressions(folder: str) -> list[str]:
    return [
        f"{path.relative_to(ROOT)}: {line.strip()}"
        for path in sorted((ROOT / folder).rglob("*.py"))
        for line in Path(path).read_text().splitlines()
        if SUPPRESSION in line
    ]


def test_uses_the_strict_preset_and_checks_unannotated_bodies() -> None:
    pyrefly = read_toml(PYREFLY_TOML)

    assert pyrefly["preset"] == "strict"
    assert pyrefly["check-unannotated-defs"] is True


def test_every_error_kind_left_off_has_a_reason() -> None:
    in_errors = False
    missing: list[str] = []
    for line in PYREFLY_TOML.read_text().splitlines():
        if line.startswith("["):
            in_errors = line in ERROR_TABLES
        elif in_errors and TURNED_OFF.search(line) is not None and "#" not in line:
            missing.append(line.strip())

    assert missing == []


def test_every_suppression_names_its_kind_and_reason() -> None:
    suppressions = [
        suppression
        for folder in ("src", "tests", "scripts", "migrations")
        for suppression in _suppressions(folder)
    ]

    assert [s for s in suppressions if WELL_FORMED.search(s) is None] == []


def test_src_has_few_suppressions() -> None:
    assert len(_suppressions("src")) <= MAX_SRC_SUPPRESSIONS
