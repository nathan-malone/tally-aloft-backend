from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis


@dataclass(frozen=True, slots=True)
class ValkeyCheck:
    """A readiness check that Valkey answers `PING` on one of the process's clients."""

    client: Redis
    name: str

    async def check(self) -> None:
        """Send `PING`, which raises unless Valkey answers."""
        _ = await self.client.ping()
