import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError

from tests.support.postgres import (
    LOCK_NOT_AVAILABLE,
    PostgresServer,
    connected,
    sqlstate,
    truncate_tables,
)
from tests.support.tables import test_rows

pytestmark = pytest.mark.container


async def _count(server: PostgresServer) -> int:
    async with connected(server.app_url) as connection:
        result = await connection.execute(select(test_rows.c.id))
        return len(result.all())


async def _commit_a_row(server: PostgresServer) -> None:
    async with connected(server.app_url) as connection:
        _ = await connection.execute(insert(test_rows).values(value="committed"))
        await connection.commit()


@pytest.mark.parametrize("run", ["first", "second"])
async def test_each_test_starts_with_empty_tables(
    database: PostgresServer, run: str
) -> None:
    assert await _count(database) == 0, run

    await _commit_a_row(database)


async def test_truncation_empties_the_tables_but_keeps_the_migration_state(
    database: PostgresServer,
) -> None:
    await _commit_a_row(database)

    await truncate_tables(database)

    assert await _count(database) == 0
    async with connected(database.owner_url) as connection:
        result = await connection.execute(
            text("SELECT version_num FROM alembic_version")
        )
        assert result.scalar_one_or_none() is not None


async def test_truncation_fails_rather_than_waits_on_an_open_transaction(
    database: PostgresServer,
) -> None:
    async with connected(database.app_url) as idle:
        _ = await idle.execute(select(test_rows.c.id))

        with pytest.raises(DBAPIError) as error:
            await truncate_tables(database, lock_timeout=1)

        await idle.rollback()

    assert sqlstate(error.value) == LOCK_NOT_AVAILABLE
