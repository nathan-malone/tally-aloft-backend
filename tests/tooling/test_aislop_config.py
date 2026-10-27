import json
import re

from tests.support.paths import ROOT

CONFIG = ROOT / ".aislop" / "config.yml"
_SETTING = re.compile(r"^(?P<indent> *)(?P<key>[\w-]+):\s*(?P<value>[^#]*?)\s*(#.*)?$")
POLICY = {
    # Ruff owns formatting and linting, and Import Linter owns architecture.
    "engines.format": "false",
    "engines.lint": "false",
    "engines.architecture": "false",
    "engines.ai-slop": "true",
    # Scans stay offline and report nothing home.
    "security.audit": "false",
    "telemetry.enabled": "false",
    # Any finding, or an engine that can't run, fails the check.
    "ci.failBelow": "100",
    "ci.baseline": ".aislop/ci-baseline.json",
    "ci.failOnMissingTools": "true",
}
BASELINE = ROOT / ".aislop" / "ci-baseline.json"


def _settings() -> dict[str, str]:
    """Return each setting of the two-level config as `section.key`, as text."""
    settings: dict[str, str] = {}
    section = ""
    for line in CONFIG.read_text().splitlines():
        match = _SETTING.match(line)
        if match is None:
            continue
        if match["indent"] != "":
            settings[f"{section}.{match['key']}"] = match["value"]
        else:
            section = match["key"]
    return settings


def test_config_keeps_the_check_policy() -> None:
    settings = _settings()

    assert {key: settings.get(key) for key in POLICY} == POLICY


def test_baseline_accepts_no_findings() -> None:
    baseline = json.loads(BASELINE.read_text())

    assert baseline == {"version": 1, "entries": []}
