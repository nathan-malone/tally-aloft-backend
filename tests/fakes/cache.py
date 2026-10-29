from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tally_aloft.application.ports.cache import check_ttl

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime, timedelta


@dataclass(slots=True)
class FakeCache:
    """An in-memory cache whose entries expire by the time `now` returns."""

    now: Callable[[], datetime]
    _entries: dict[str, tuple[bytes, datetime]] = field(
        default_factory=dict, init=False, repr=False
    )

    async def get(self, key: str) -> bytes | None:
        """Return the value stored under `key`, or None if it is missing or expired."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if self.now() >= expires_at:
            del self._entries[key]
            return None
        return value

    async def set(self, key: str, value: bytes, ttl: timedelta) -> None:
        """Store `value` under `key` until `ttl` has passed."""
        check_ttl(ttl)
        self._entries[key] = (value, self.now() + ttl)

    async def delete(self, key: str) -> None:
        """Remove `key`'s value. A missing key is not an error."""
        _ = self._entries.pop(key, None)
