import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.support.migrations import alembic_config, real_head, upgrade, write_revision
from tests.support.postgres import INSUFFICIENT_PRIVILEGE, connected, sqlstate

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.postgres import PostgresServer

pytestmark = pytest.mark.container


async def _run_as_app(server: PostgresServer, statement: str) -> None:
    async with connected(server.app_url) as connection:
        _ = await connection.execute(text(statement))
        await connection.commit()


async def _sqlstate_as_app(server: PostgresServer, statement: str) -> str | None:
    with pytest.raises(DBAPIError) as error:
        await _run_as_app(server, statement)
    return sqlstate(error.value)


@pytest.mark.parametrize(
    "statement",
    ["CREATE TABLE x (id int)", "CREATE SCHEMA x", "CREATE TEMP TABLE x (id int)"],
)
async def test_the_app_role_can_create_nothing(
    postgres_server: PostgresServer, statement: str
) -> None:
    assert await _sqlstate_as_app(postgres_server, statement) == INSUFFICIENT_PRIVILEGE


async def test_new_tables_are_append_only_until_a_migration_grants_more(
    fresh_database: PostgresServer, tmp_path: Path
) -> None:
    write_revision(
        tmp_path,
        revision="create_ledger",
        down_revision=real_head(),
        upgrade='op.execute("CREATE TABLE ledger (id int, note text)")',
    )
    write_revision(
        tmp_path,
        revision="grant_update",
        down_revision="create_ledger",
        upgrade='op.execute("GRANT UPDATE ON ledger TO tally_aloft_app")',
    )
    config = alembic_config(fresh_database.owner_url, extra_versions=tmp_path)
    await asyncio.to_thread(upgrade, config, "create_ledger")

    await _run_as_app(fresh_database, "INSERT INTO ledger VALUES (1, 'first')")
    await _run_as_app(fresh_database, "SELECT * FROM ledger")
    update = "UPDATE ledger SET note = 'changed' WHERE id = 1"
    assert await _sqlstate_as_app(fresh_database, update) == INSUFFICIENT_PRIVILEGE
    delete = "DELETE FROM ledger WHERE id = 1"
    assert await _sqlstate_as_app(fresh_database, delete) == INSUFFICIENT_PRIVILEGE

    await asyncio.to_thread(upgrade, config, "grant_update")

    await _run_as_app(fresh_database, update)
    assert await _sqlstate_as_app(fresh_database, delete) == INSUFFICIENT_PRIVILEGE
