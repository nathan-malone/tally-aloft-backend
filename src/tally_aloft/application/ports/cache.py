from datetime import timedelta
from typing import Protocol

# The shortest time to live an entry may have. Valkey counts it in whole
# milliseconds, so anything shorter would never be stored.
MIN_CACHE_TTL = timedelta(milliseconds=1)
_TTL_TOO_SHORT = "a cache entry's time to live must be at least 1 millisecond"


class CacheUnavailableError(Exception):
    """The cache's store failed, refused the connection or timed out."""


def check_ttl(ttl: timedelta) -> None:
    """Raise `ValueError` if `ttl` is shorter than `MIN_CACHE_TTL`."""
    if ttl < MIN_CACHE_TTL:
        raise ValueError(_TTL_TOO_SHORT)


class Cache(Protocol):
    """
    Short-lived `bytes` values under string keys, each with a time to live.

    Keys are the caller's own: the adapter keeps them apart from other users
    of the store. Every entry expires, so cached personal data lasts only
    until its TTL runs out or its key is deleted; keep such TTLs short, or
    delete the key when the data is deleted. A store that fails or stalls
    raises `CacheUnavailableError`.
    """

    async def get(self, key: str) -> bytes | None:
        """Return the value stored under `key`, or None if it is missing or expired."""
        ...

    async def set(self, key: str, value: bytes, ttl: timedelta) -> None:
        """
        Store `value` under `key` for `ttl`, replacing any value already there.

        A `ttl` shorter than `MIN_CACHE_TTL` raises `ValueError`: that is a
        programming error, not one the API reports.
        """
        ...

    async def delete(self, key: str) -> None:
        """Remove `key`'s value. A missing key is not an error."""
        ...
