"""
The table models, the only place SQLAlchemy tables are declared.

Each table is a model on `Base` in its own module here. Migrations read the
tables through `load_metadata()`.
"""

import importlib
from pathlib import Path
from types import MappingProxyType
from typing import Final

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Stable constraint names, so autogenerate and hand-written migrations agree.
# A check constraint must be named, which `%(constraint_name)s` enforces.
NAMING_CONVENTION: Final = MappingProxyType(
    {
        "pk": "pk_%(table_name)s",
        "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    }
)


class Base(DeclarativeBase):
    """The base of every table model, so all tables share one `MetaData`."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def load_metadata() -> MetaData:
    """
    Import every table module, then return the `MetaData` that holds them.

    Autogenerate drops any table missing from the metadata, so a module that
    nothing else imported must still be loaded.
    """
    for path in sorted(Path(__file__).parent.glob("*.py")):
        if path.stem != "__init__":
            _ = importlib.import_module(f"{__name__}.{path.stem}")
    return Base.metadata
