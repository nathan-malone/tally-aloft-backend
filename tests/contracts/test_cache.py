import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, cast

import pytest

from tally_aloft.adapters.outbound.redis.cache import ValkeyCache
from tally_aloft.adapters.outbound.redis.client import build_client
from tests.fakes.cache import FakeCache
from tests.fakes.clock import FakeClock

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from tally_aloft.application.ports.cache import Cache

TTL = timedelta(seconds=1)
# Each tick moves the fake's time 100 ms or waits 50 ms on Valkey, so expiry
# gets 6 simulated seconds on the fake and about 3 real seconds on Valkey.
MAX_TICKS = 60


@dataclass(frozen=True, slots=True)
class CacheCase:
    """A cache under test, and how to let time pass for it."""

    cache: Cache
    tick: Callable[[], Awaitable[None]]


@pytest.fixture
def fake_case() -> CacheCase:
    time = FakeClock()

    async def tick() -> None:
        time.advance(timedelta(milliseconds=100))

    return CacheCase(cache=FakeCache(now=time.now), tick=tick)


@pytest.fixture
async def valkey_case(valkey_url: str) -> AsyncGenerator[CacheCase]:
    client = build_client(valkey_url)

    async def tick() -> None:
        await asyncio.sleep(0.05)

    yield CacheCase(cache=ValkeyCache(client), tick=tick)
    await client.aclose()


# Parametrized by fixture name, because the Valkey case is an async fixture
# and pytest can't parametrize over fixtures directly.
@pytest.fixture(
    params=["fake_case", pytest.param("valkey_case", marks=pytest.mark.container)]
)
def case(request: pytest.FixtureRequest) -> CacheCase:
    return cast("CacheCase", request.getfixturevalue(cast("str", request.param)))


async def test_get_returns_what_set_stored(case: CacheCase) -> None:
    await case.cache.set("k", b"v", TTL)

    assert await case.cache.get("k") == b"v"


async def test_get_of_a_missing_key_returns_none(case: CacheCase) -> None:
    assert await case.cache.get("missing") is None


async def test_set_replaces_the_value(case: CacheCase) -> None:
    await case.cache.set("k", b"first", TTL)
    await case.cache.set("k", b"second", TTL)

    assert await case.cache.get("k") == b"second"


async def test_delete_removes_the_value(case: CacheCase) -> None:
    await case.cache.set("k", b"v", TTL)

    await case.cache.delete("k")

    assert await case.cache.get("k") is None


async def test_delete_of_a_missing_key_does_nothing(case: CacheCase) -> None:
    await case.cache.delete("missing")

    assert await case.cache.get("missing") is None


async def test_a_value_expires_after_its_ttl(case: CacheCase) -> None:
    await case.cache.set("k", b"v", TTL)
    assert await case.cache.get("k") == b"v"

    for _ in range(MAX_TICKS):
        await case.tick()
        if await case.cache.get("k") is None:
            return
    pytest.fail(f"the value outlived {MAX_TICKS} ticks after a {TTL} TTL")


@pytest.mark.parametrize(
    "ttl",
    [timedelta(microseconds=500), timedelta(0), timedelta(seconds=-1)],
    ids=["500us", "zero", "negative"],
)
async def test_a_ttl_under_a_millisecond_is_rejected(
    case: CacheCase, ttl: timedelta
) -> None:
    with pytest.raises(ValueError, match="at least 1 millisecond"):
        await case.cache.set("k", b"v", ttl)

    assert await case.cache.get("k") is None


async def test_the_fake_expires_a_value_exactly_at_its_ttl() -> None:
    # Only the fake's time is exact, so only the fake can be held to the boundary.
    time = FakeClock()
    cache = FakeCache(now=time.now)
    await cache.set("k", b"v", TTL)

    time.advance(TTL - timedelta(milliseconds=1))
    assert await cache.get("k") == b"v"
    time.advance(timedelta(milliseconds=1))
    assert await cache.get("k") is None
