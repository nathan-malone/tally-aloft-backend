import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import DBAPIError

from tests.support.migrations import (
    alembic_config,
    current_revision,
    real_head,
    upgrade,
    write_revision,
)
from tests.support.postgres import (
    DATABASE,
    INSUFFICIENT_PRIVILEGE,
    execute,
    sqlstate,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.postgres import PostgresDatabase

pytestmark = pytest.mark.container


async def _sqlstate_as_app(database: PostgresDatabase, statement: str) -> str | None:
    with pytest.raises(DBAPIError) as error:
        await execute(database.app_url, statement)
    return sqlstate(error.value)


@pytest.mark.parametrize(
    "statement",
    ["CREATE TABLE x (id int)", "CREATE SCHEMA x", "CREATE TEMP TABLE x (id int)"],
)
async def test_the_app_role_can_create_nothing(
    postgres_server: PostgresDatabase, statement: str
) -> None:
    assert await _sqlstate_as_app(postgres_server, statement) == INSUFFICIENT_PRIVILEGE


async def test_new_tables_are_append_only_until_a_migration_grants_more(
    fresh_database: PostgresDatabase, tmp_path: Path
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

    await execute(fresh_database.app_url, "INSERT INTO ledger VALUES (1, 'first')")
    await execute(fresh_database.app_url, "SELECT * FROM ledger")
    update = "UPDATE ledger SET note = 'changed' WHERE id = 1"
    assert await _sqlstate_as_app(fresh_database, update) == INSUFFICIENT_PRIVILEGE
    delete = "DELETE FROM ledger WHERE id = 1"
    assert await _sqlstate_as_app(fresh_database, delete) == INSUFFICIENT_PRIVILEGE

    await asyncio.to_thread(upgrade, config, "grant_update")

    await execute(fresh_database.app_url, update)
    assert await _sqlstate_as_app(fresh_database, delete) == INSUFFICIENT_PRIVILEGE


@pytest.mark.parametrize(
    ("as_superuser", "on_database", "undo"),
    [
        (('GRANT CREATE ON DATABASE "{name}" TO tally_aloft_app',), False, None),
        (
            (
                "ALTER SCHEMA public OWNER TO postgres",
                "GRANT CREATE ON SCHEMA public TO PUBLIC",
            ),
            True,
            None,
        ),
        (
            ("ALTER ROLE tally_aloft_app CREATEDB",),
            False,
            "ALTER ROLE tally_aloft_app NOCREATEDB",
        ),
    ],
    ids=["database-create", "public-owned-elsewhere", "role-attribute"],
)
async def test_the_grants_revision_fails_while_the_app_role_could_create_objects(
    fresh_database: PostgresDatabase,
    as_superuser: tuple[str, ...],
    *,
    on_database: bool,
    undo: str | None,
) -> None:
    # Roles are cluster-wide, so a changed role is put back for later tests.
    target = fresh_database if on_database else fresh_database.on(DATABASE)
    for statement in as_superuser:
        await execute(target.superuser_url, statement.format(name=fresh_database.name))
    config = alembic_config(fresh_database.owner_url)
    try:
        with pytest.raises(DBAPIError, match="tally_aloft_app can still create"):
            await asyncio.to_thread(upgrade, config)
    finally:
        if undo is not None:
            await execute(fresh_database.superuser_url, undo)

    assert await current_revision(fresh_database) is None


async def test_the_grants_revision_runs_only_as_the_owner(
    fresh_database: PostgresDatabase,
) -> None:
    config = alembic_config(fresh_database.superuser_url)

    with pytest.raises(DBAPIError, match="must run as tally_aloft_owner"):
        await asyncio.to_thread(upgrade, config)

    assert await current_revision(fresh_database) is None
