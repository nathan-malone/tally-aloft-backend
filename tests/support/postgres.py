"""
Real Postgres for the tests, in a testcontainers container.

The container runs Compose's own init script, so its roles match local
development's. Tests connect as the application role, and the owner role runs
the migrations and the clean-up.
"""

from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from sqlalchemy.engine import URL
from testcontainers.community.postgres import PostgresContainer

from tests.support.paths import ROOT

if TYPE_CHECKING:
    from collections.abc import Generator

    from sqlalchemy.exc import DBAPIError

# The one place the tests' Postgres version is set; compose.yaml must match it.
POSTGRES_IMAGE = "postgres:18"
DATABASE = "tally_aloft"
SUPERUSER = "postgres"
OWNER_ROLE = "tally_aloft_owner"
APP_ROLE = "tally_aloft_app"
INIT_SCRIPT = ROOT / "compose" / "postgres-init.sql"
_PORT = 5432

# SQLSTATE codes the tests expect.
INSUFFICIENT_PRIVILEGE = "42501"
QUERY_CANCELED = "57014"
LOCK_NOT_AVAILABLE = "55P03"


@dataclass(frozen=True, slots=True)
class PostgresServer:
    """Where a test Postgres listens, and the URL of each role on a database."""

    host: str
    port: int
    database: str = DATABASE

    def url(self, role: str) -> str:
        """Return `role`'s asyncpg URL, with the local password the init script set."""
        password = SUPERUSER if role == SUPERUSER else role
        return URL.create(
            "postgresql+asyncpg",
            username=role,
            password=password,
            host=self.host,
            port=self.port,
            database=self.database,
        ).render_as_string(hide_password=False)

    @property
    def superuser_url(self) -> str:
        """Return the bootstrap superuser's URL, for creating databases."""
        return self.url(SUPERUSER)

    @property
    def owner_url(self) -> str:
        """Return the owner role's URL, which migrations use."""
        return self.url(OWNER_ROLE)

    @property
    def app_url(self) -> str:
        """Return the application role's URL, which tests connect with."""
        return self.url(APP_ROLE)

    def on(self, database: str) -> PostgresServer:
        """Return this server with `database` in place of the default one."""
        return replace(self, database=database)


@contextmanager
def running_postgres() -> Generator[PostgresServer]:
    """Start Postgres with the Compose init script, and stop it on exit."""
    container = PostgresContainer(
        POSTGRES_IMAGE,
        username=SUPERUSER,
        password=SUPERUSER,
        dbname=DATABASE,
        driver=None,
    ).with_volume_mapping(
        str(INIT_SCRIPT), "/docker-entrypoint-initdb.d/init.sql", "ro"
    )
    # testcontainers waits with psql over TCP, which the image's initialization
    # server doesn't listen on, so the init script has run by the time it's up.
    with container:
        yield PostgresServer(
            host=container.get_container_host_ip(),
            port=container.get_exposed_port(_PORT),
        )


def sqlstate(error: DBAPIError) -> str | None:
    """Return the SQLSTATE that SQLAlchemy's asyncpg adapter puts on the error."""
    code: object = getattr(error.orig, "sqlstate", None)
    return code if isinstance(code, str) else None
