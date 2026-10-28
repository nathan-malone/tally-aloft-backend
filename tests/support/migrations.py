"""
Run Alembic in the tests, with the real `env.py`.

Test migrations are written into a test's own directory, added to the real
versions as an extra version location and chained on the real head, so the
real `env.py`, with its lock and statement timeouts, runs them too.
"""

import os
import textwrap
from typing import TYPE_CHECKING

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from tally_aloft.bootstrap.settings import MigrationSettings
from tests.support.paths import ROOT
from tests.support.postgres import connected

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.postgres import PostgresDatabase

ALEMBIC_INI = ROOT / "alembic.ini"
VERSIONS = ROOT / "migrations" / "versions"
_REVISION = '''\
"""Test revision {revision}."""

import sqlalchemy as sa
from alembic import op

revision = {revision!r}
down_revision = {down_revision!r}
branch_labels = None
depends_on = None


def upgrade() -> None:
{upgrade}


def downgrade() -> None:
    pass
'''


def alembic_config(
    owner_url: str, *, lock_timeout: int = 5, extra_versions: Path | None = None
) -> Config:
    """
    Return the real Alembic config, connecting as `owner_url`.

    The settings are passed in, so the environment is never read, and logging
    is left to pytest. `extra_versions` adds a directory of test migrations.
    """
    config = Config(ALEMBIC_INI)
    config.attributes["migration_settings"] = MigrationSettings.for_tests(
        migration_database_url=owner_url, migration_lock_timeout=lock_timeout
    )
    config.attributes["configure_logging"] = False
    if extra_versions is not None:
        locations = os.pathsep.join([str(VERSIONS), str(extra_versions)])
        config.set_main_option("version_locations", locations)
    return config


def real_head() -> str:
    """Return the head of the committed migrations."""
    head = ScriptDirectory.from_config(Config(ALEMBIC_INI)).get_current_head()
    assert head is not None
    return head


async def current_revision(database: PostgresDatabase) -> str | None:
    """Return the database's migration revision, or None before the first one."""
    async with connected(database.owner_url) as connection:
        exists = await connection.execute(text("SELECT to_regclass('alembic_version')"))
        if exists.scalar_one() is None:
            return None
        result = await connection.execute(
            text("SELECT version_num FROM alembic_version")
        )
        return result.scalar_one_or_none()


def upgrade(config: Config, revision: str = "head") -> None:
    """
    Upgrade to `revision`.

    `env.py` calls `asyncio.run`, so async tests call this through
    `asyncio.to_thread`, whose thread has no running loop.
    """
    command.upgrade(config, revision)


def downgrade(config: Config, revision: str) -> None:
    """Downgrade to `revision`, as `upgrade` does."""
    command.downgrade(config, revision)


def write_revision(
    directory: Path, *, revision: str, down_revision: str, upgrade: str
) -> None:
    """Write a test revision whose `upgrade()` body is the source `upgrade`."""
    body = textwrap.indent(textwrap.dedent(upgrade).strip(), "    ")
    source = _REVISION.format(
        revision=revision, down_revision=down_revision, upgrade=body
    )
    _ = (directory / f"{revision}.py").write_text(source)
