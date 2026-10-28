from typing import TYPE_CHECKING

import pytest

from tests.support.postgres import PostgresServer, running_postgres

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(scope="session")
def postgres_server() -> Iterator[PostgresServer]:
    """
    Start one Postgres per test worker, on first use only.

    Tests that use it need Docker, so they're marked `container`.
    """
    with running_postgres() as server:
        yield server
