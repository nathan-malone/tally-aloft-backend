import sys
from pathlib import Path

from tally_aloft.adapters.outbound.postgres import tables
from tally_aloft.adapters.outbound.postgres.tables import Base, load_metadata


def test_load_metadata_imports_every_table_module() -> None:
    package = Path(tables.__file__).parent
    modules = {
        f"{tables.__name__}.{path.stem}"
        for path in package.glob("*.py")
        if path.stem != "__init__"
    }

    metadata = load_metadata()

    assert metadata is Base.metadata
    assert modules <= sys.modules.keys()
