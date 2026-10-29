from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from redis.exceptions import RedisError

from tally_aloft.application.ports.cache import CacheUnavailableError, check_ttl

if TYPE_CHECKING:
    from redis.asyncio import Redis

# Keeps the cache's keys apart from the broker's on the shared instance.
_KEY_PREFIX = "cache:"
_MILLISECOND = timedelta(milliseconds=1)


@dataclass(frozen=True, slots=True)
class ValkeyCache:
    """The `Cache` port on Valkey, under keys prefixed `cache:`."""

    client: Redis

    async def get(self, key: str) -> bytes | None:
        """Return the value stored under `key`, or None if it is missing or expired."""
        try:
            value = await self.client.get(_KEY_PREFIX + key)
        except RedisError as error:
            raise CacheUnavailableError from error
        # The client never decodes replies (tests/unit/adapters/test_redis_client.py
        # pins it), so a value is always bytes.
        return value if isinstance(value, bytes) else None

    async def set(self, key: str, value: bytes, ttl: timedelta) -> None:
        """Store `value` under `key` for `ttl`, in whole milliseconds rounded down."""
        check_ttl(ttl)
        try:
            _ = await self.client.set(_KEY_PREFIX + key, value, px=ttl // _MILLISECOND)
        except RedisError as error:
            raise CacheUnavailableError from error

    async def delete(self, key: str) -> None:
        """Remove `key`'s value. A missing key is not an error."""
        try:
            _ = await self.client.delete(_KEY_PREFIX + key)
        except RedisError as error:
            raise CacheUnavailableError from error
