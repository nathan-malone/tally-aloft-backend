import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"


def pyproject() -> dict[str, object]:
    """Return the parsed `pyproject.toml`."""
    return tomllib.loads(PYPROJECT.read_text())
