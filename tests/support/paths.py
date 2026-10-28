import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
RUFF_TOML = ROOT / "ruff.toml"
PYREFLY_TOML = ROOT / "pyrefly.toml"
IMPORT_LINTER_CONFIG = ROOT / ".importlinter"


def read_toml(path: Path) -> dict[str, object]:
    """Return the parsed TOML file at `path`."""
    return tomllib.loads(path.read_text())


def pyproject() -> dict[str, object]:
    """Return the parsed `pyproject.toml`."""
    return read_toml(PYPROJECT)
