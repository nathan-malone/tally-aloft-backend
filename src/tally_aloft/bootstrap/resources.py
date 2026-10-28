from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tally_aloft.adapters.outbound.postgres.engine import build_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncEngine

    from tally_aloft.bootstrap.settings import Settings


@dataclass(frozen=True, slots=True, kw_only=True)
class Resources:
    """The shared clients a process opens once, which its adapters are built on."""

    engine: AsyncEngine


@asynccontextmanager
async def open_resources(settings: Settings) -> AsyncGenerator[Resources]:
    """
    Open the shared resources, and close them, last first, on exit.

    This is the one place that opens them, for the API's lifespan and the
    worker's startup alike. They close even when the body raises.
    """
    async with AsyncExitStack() as stack:
        engine = build_engine(
            settings.database_url.get_secret_value(),
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            statement_timeout_seconds=settings.database_statement_timeout,
        )
        _ = stack.push_async_callback(engine.dispose)
        yield Resources(engine=engine)
