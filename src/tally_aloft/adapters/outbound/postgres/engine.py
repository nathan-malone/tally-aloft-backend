from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import create_async_engine

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncEngine

# How long asyncpg waits for a connection to open, so a stalled server raises
# rather than hangs. The services and the migrations both use it.
CONNECT_TIMEOUT_SECONDS = 5
# How long a session waits for a pooled connection before it raises.
# SQLAlchemy's default, set here because job tasks' backstop is sized from it.
POOL_TIMEOUT_SECONDS = 30


def connect_args(server_settings: Mapping[str, str]) -> dict[str, object]:
    """
    Return asyncpg's connect arguments, with these session settings.

    asyncpg sends server settings as the connection opens, so each holds for
    the whole session, as `SET` does (never `SET LOCAL`), on every connection.
    """
    return {
        "timeout": CONNECT_TIMEOUT_SECONDS,
        "server_settings": dict(server_settings),
    }


def build_engine(
    url: str, *, pool_size: int, max_overflow: int, statement_timeout_seconds: int
) -> AsyncEngine:
    """
    Return the application role's engine, which connects only when first used.

    Errors and logs never show statement parameters, which can hold pilots'
    data, and every statement is cancelled after the timeout.
    """
    return create_async_engine(
        url,
        hide_parameters=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=POOL_TIMEOUT_SECONDS,
        connect_args=connect_args(
            {"statement_timeout": f"{statement_timeout_seconds}s"}
        ),
    )
