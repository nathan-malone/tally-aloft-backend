import importlib
import pkgutil

import tally_aloft
from tests.support.paths import ROOT

# Entry points read settings at import, and define no errors or job kinds.
ENTRY_POINTS = {"tally_aloft.main", "tally_aloft.worker"}


def source_modules() -> set[str]:
    """Return the name of every module under `src/`, but the entry points."""
    package = ROOT / "src" / "tally_aloft"
    names: set[str] = set()
    for path in package.rglob("*.py"):
        parts = path.relative_to(package.parent).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.add(".".join(parts))
    return names - {"tally_aloft"} - ENTRY_POINTS


def import_every_module() -> set[str]:
    """Import every `tally_aloft` module but the entry points; return their names."""
    walked = {
        module.name
        for module in pkgutil.walk_packages(tally_aloft.__path__, "tally_aloft.")
        if module.name not in ENTRY_POINTS
    }
    for name in walked:
        _ = importlib.import_module(name)
    return walked
