import asyncio
import os
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata, produce_migrations
from alembic.operations.ops import CreateTableOp
from alembic.runtime.migration import MigrationContext
from sqlalchemy import (
    Column,
    Constraint,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.exc import DBAPIError

from tally_aloft.adapters.outbound.postgres.tables import (
    NAMING_CONVENTION,
    Base,
    load_metadata,
)
from tests.support.migrations import (
    alembic_config,
    current_revision,
    downgrade,
    real_head,
    upgrade,
    write_revision,
)
from tests.support.paths import ROOT
from tests.support.postgres import LOCK_NOT_AVAILABLE, connected, execute, sqlstate
from tests.support.pyrefly import pyrefly
from tests.support.ruff import ruff
from tests.support.server import without_tally_variables

if TYPE_CHECKING:
    from pathlib import Path

    from alembic.operations.ops import MigrationScript
    from sqlalchemy.engine import Connection

    from tests.support.postgres import PostgresDatabase

# AC 7: a migration stuck on a lock fails within this, with a 1-second timeout.
LOCK_DEADLINE_SECONDS = 3


@pytest.mark.container
async def test_upgrade_reaches_the_head(fresh_database: PostgresDatabase) -> None:
    await asyncio.to_thread(upgrade, alembic_config(fresh_database.owner_url))

    assert await current_revision(fresh_database) == real_head()


@pytest.mark.container
async def test_downgrade_to_base_then_upgrade_again(
    fresh_database: PostgresDatabase,
) -> None:
    config = alembic_config(fresh_database.owner_url)
    await asyncio.to_thread(upgrade, config)

    await asyncio.to_thread(downgrade, config, "base")
    assert await current_revision(fresh_database) is None
    await asyncio.to_thread(upgrade, config)

    assert await current_revision(fresh_database) == real_head()


@pytest.mark.container
async def test_migrations_set_the_lock_timeout_and_no_statement_timeout(
    fresh_database: PostgresDatabase, tmp_path: Path
) -> None:
    database = fresh_database.name
    await execute(
        fresh_database.owner_url,
        f"ALTER DATABASE \"{database}\" SET statement_timeout = '1s'",
    )
    async with connected(fresh_database.owner_url) as connection:
        default = await connection.execute(text("SHOW statement_timeout"))
        assert default.scalar_one() == "1s"
    write_revision(
        tmp_path,
        revision="timeouts",
        down_revision=real_head(),
        upgrade="""
            bind = op.get_bind()
            statement_timeout = bind.exec_driver_sql("SHOW statement_timeout")
            assert statement_timeout.scalar_one() == "0"
            assert bind.exec_driver_sql("SHOW lock_timeout").scalar_one() == "1s"
            op.execute("SELECT pg_sleep(2)")
        """,
    )
    config = alembic_config(
        fresh_database.owner_url, lock_timeout=1, extra_versions=tmp_path
    )

    await asyncio.to_thread(upgrade, config)

    assert await current_revision(fresh_database) == "timeouts"


@pytest.mark.container
@pytest.mark.parametrize("in_autocommit_block", [False, True])
async def test_a_migration_waiting_on_a_lock_fails_fast(
    fresh_database: PostgresDatabase, tmp_path: Path, *, in_autocommit_block: bool
) -> None:
    write_revision(
        tmp_path,
        revision="create_locked",
        down_revision=real_head(),
        upgrade='op.execute("CREATE TABLE locked (id int)")',
    )
    alter = 'op.execute("ALTER TABLE locked ADD COLUMN extra int")'
    if in_autocommit_block:
        alter = f"with op.get_context().autocommit_block():\n    {alter}"
    write_revision(
        tmp_path, revision="alter_locked", down_revision="create_locked", upgrade=alter
    )
    config = alembic_config(
        fresh_database.owner_url, lock_timeout=1, extra_versions=tmp_path
    )
    await asyncio.to_thread(upgrade, config, "create_locked")

    async with connected(fresh_database.owner_url) as holder:
        _ = await holder.execute(text("LOCK TABLE locked IN ACCESS EXCLUSIVE MODE"))
        started = time.monotonic()
        with pytest.raises(DBAPIError) as error:
            await asyncio.to_thread(upgrade, config, "alter_locked")
        elapsed = time.monotonic() - started
        await holder.rollback()

    assert sqlstate(error.value) == LOCK_NOT_AVAILABLE
    assert elapsed < LOCK_DEADLINE_SECONDS


# The concurrent index build the README and the design docs describe.
_BUILD_INDEX_CONCURRENTLY = """
    with op.get_context().autocommit_block():
        valid: bool | None = op.get_bind().scalar(
            sa.text(
                "SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:name)"
            ),
            {"name": "ix_indexed_id"},
        )
        if valid is False:
            op.drop_index("ix_indexed_id", postgresql_concurrently=True)
        if valid is not True:
            op.create_index(
                "ix_indexed_id", "indexed", ["id"], postgresql_concurrently=True
            )
"""


def _index_revisions(directory: Path, create_indexed: str) -> None:
    """Write `create_indexed`'s revision, then the concurrent build after it."""
    write_revision(
        directory,
        revision="create_indexed",
        down_revision=real_head(),
        upgrade=create_indexed,
    )
    write_revision(
        directory,
        revision="index_concurrently",
        down_revision="create_indexed",
        upgrade=_BUILD_INDEX_CONCURRENTLY,
    )


async def _index_state(database: PostgresDatabase) -> tuple[int, bool]:
    """Return `ix_indexed_id`'s OID, and whether it is valid."""
    async with connected(database.owner_url) as connection:
        result = await connection.execute(
            text(
                "SELECT indexrelid::int, indisvalid FROM pg_index "
                "WHERE indexrelid = to_regclass('ix_indexed_id')"
            )
        )
        oid, valid = result.one()
        return oid, valid


@pytest.mark.container
async def test_an_index_builds_concurrently_in_an_autocommit_block(
    fresh_database: PostgresDatabase, tmp_path: Path
) -> None:
    _index_revisions(tmp_path, 'op.execute("CREATE TABLE indexed (id int)")')
    config = alembic_config(fresh_database.owner_url, extra_versions=tmp_path)

    await asyncio.to_thread(upgrade, config)

    async with connected(fresh_database.owner_url) as connection:
        result = await connection.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'indexed'")
        )
        assert result.scalars().all() == ["ix_indexed_id"]
    assert (await _index_state(fresh_database))[1] is True


@pytest.mark.container
async def test_a_concurrent_build_keeps_a_valid_index(
    fresh_database: PostgresDatabase, tmp_path: Path
) -> None:
    # As though an operator built it ahead of the deploy.
    _index_revisions(
        tmp_path,
        """
        op.execute("CREATE TABLE indexed (id int)")
        op.execute("CREATE INDEX ix_indexed_id ON indexed (id)")
        """,
    )
    config = alembic_config(fresh_database.owner_url, extra_versions=tmp_path)
    await asyncio.to_thread(upgrade, config, "create_indexed")
    before = await _index_state(fresh_database)

    await asyncio.to_thread(upgrade, config)

    assert await _index_state(fresh_database) == before


@pytest.mark.container
async def test_a_concurrent_build_replaces_an_invalid_index(
    fresh_database: PostgresDatabase, tmp_path: Path
) -> None:
    _index_revisions(
        tmp_path,
        """
        op.execute("CREATE TABLE indexed (id int)")
        op.execute("INSERT INTO indexed VALUES (1), (1)")
        """,
    )
    config = alembic_config(fresh_database.owner_url, extra_versions=tmp_path)
    await asyncio.to_thread(upgrade, config, "create_indexed")
    # A failed concurrent build leaves its index behind, INVALID.
    with pytest.raises(DBAPIError):
        await execute(
            fresh_database.owner_url,
            "CREATE UNIQUE INDEX CONCURRENTLY ix_indexed_id ON indexed (id)",
            autocommit=True,
        )
    invalid_oid, valid = await _index_state(fresh_database)
    assert valid is False

    await asyncio.to_thread(upgrade, config)

    oid, valid = await _index_state(fresh_database)
    assert valid is True
    assert oid != invalid_oid


def _convention_tables() -> MetaData:
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    _ = Table(
        "parents",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False),
        UniqueConstraint("code"),
    )
    _ = Table(
        "children",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("parent_id", ForeignKey("parents.id"), nullable=False),
    )
    return metadata


@pytest.mark.container
async def test_autogenerate_names_constraints_by_the_convention(
    fresh_database: PostgresDatabase,
) -> None:
    await asyncio.to_thread(upgrade, alembic_config(fresh_database.owner_url))
    metadata = _convention_tables()

    def produce(connection: Connection) -> MigrationScript:
        return produce_migrations(MigrationContext.configure(connection), metadata)

    async with connected(fresh_database.owner_url) as connection:
        script = await connection.run_sync(produce)

    assert script.upgrade_ops is not None
    names = {
        str(part.name)
        for operation in script.upgrade_ops.ops
        if isinstance(operation, CreateTableOp)
        for part in operation.columns
        if isinstance(part, Constraint)
    }
    assert names == {
        "pk_parents",
        "uq_parents_code",
        "pk_children",
        "fk_children_parent_id_parents",
    }
    assert dict(Base.metadata.naming_convention) == dict(NAMING_CONVENTION)


def _only_revision(directory: Path) -> tuple[str, str]:
    """Return the in-tree name and the source of the one revision in `directory`."""
    [revision] = directory.glob("*.py")
    return f"migrations/versions/{revision.name}", revision.read_text()


@pytest.mark.container
async def test_a_generated_revision_passes_the_linter_formatter_and_type_checker(
    fresh_database: PostgresDatabase, tmp_path: Path
) -> None:
    config = alembic_config(fresh_database.owner_url, extra_versions=tmp_path)
    await asyncio.to_thread(upgrade, config)
    # A table only autogenerate sees, so it writes a real create_table.
    table = Table(
        "generated_probe",
        Base.metadata,
        Column("id", Integer, primary_key=True),
        Column("a_rather_long_column_name_for_the_line_length", Text, nullable=False),
        UniqueConstraint("a_rather_long_column_name_for_the_line_length"),
    )
    try:
        _ = await asyncio.to_thread(
            command.revision,
            config,
            message="add generated probe",
            autogenerate=True,
            version_path=str(tmp_path),
        )
    finally:
        Base.metadata.remove(table)

    filename, source = _only_revision(tmp_path)
    lint = ruff("check", source=source, filename=filename)
    form = ruff("format", "--check", source=source, filename=filename)
    types = pyrefly(source=source, filename=filename)

    assert "op.create_table" in source
    assert lint.returncode == 0, lint.stdout
    assert form.returncode == 0, form.stdout
    assert types.returncode == 0, types.stdout + types.stderr


def test_upgrade_without_the_owner_url_names_the_variable() -> None:
    env = without_tally_variables(os.environ)

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode != 0
    assert "TALLY_MIGRATION_DATABASE_URL (missing)" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.container
async def test_the_migrations_match_the_table_models(
    fresh_database: PostgresDatabase,
) -> None:
    await asyncio.to_thread(upgrade, alembic_config(fresh_database.owner_url))
    metadata = load_metadata()

    def differences(connection: Connection) -> list[object]:
        return list(compare_metadata(MigrationContext.configure(connection), metadata))

    async with connected(fresh_database.owner_url) as connection:
        found = await connection.run_sync(differences)

    assert found == []
