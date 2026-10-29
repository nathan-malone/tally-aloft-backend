"""
Test-only tables, never on the production `Base.metadata`.

The test Postgres creates them as the owner role after the migrations, so the
default privileges give the application role `SELECT` and `INSERT`, as they
would for a migrated table.
"""

from sqlalchemy import Column, MetaData, Table, Text, Uuid, text

from tally_aloft.adapters.outbound.postgres.tables import NAMING_CONVENTION
from tests.support.postgres import connected

test_metadata = MetaData(naming_convention=NAMING_CONVENTION)

test_rows = Table(
    "test_rows",
    test_metadata,
    Column("id", Uuid, primary_key=True, server_default=text("uuidv7()")),
    Column("value", Text, nullable=False),
)


async def create_test_tables(owner_url: str) -> None:
    """Create every test-only table as the owner role."""
    async with connected(owner_url) as connection:
        await connection.run_sync(test_metadata.create_all)
        await connection.commit()
