from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True, slots=True)
class PostgresCheck:
    """The readiness check that Postgres answers on the application's engine."""

    engine: AsyncEngine

    @property
    def name(self) -> str:
        """Return the name readiness reports when this check fails."""
        return "postgres"

    async def check(self) -> None:
        """Run `SELECT 1` on a pooled connection."""
        async with self.engine.connect() as connection:
            _ = await connection.execute(text("SELECT 1"))
