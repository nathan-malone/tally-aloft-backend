import time
from datetime import timedelta

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from tally_aloft.adapters.outbound.redis.cache import ValkeyCache
from tally_aloft.adapters.outbound.redis.client import TIMEOUT_SECONDS, build_client
from tally_aloft.application.ports.cache import CacheUnavailableError
from tests.support.server import free_port
from tests.support.valkey import running_valkey


@pytest.mark.container
async def test_set_stores_the_value_under_the_prefix_with_a_ttl(
    valkey_url: str,
) -> None:
    client = build_client(valkey_url)
    try:
        await ValkeyCache(client).set("k", b"v", timedelta(seconds=10))
        stored = await client.get("cache:k")
        ttl_milliseconds = await client.pttl("cache:k")
        unprefixed = await client.exists("k")
    finally:
        await client.aclose()

    assert stored == b"v"
    assert 0 < ttl_milliseconds <= 10_000
    assert unprefixed == 0


@pytest.mark.container
async def test_a_stalled_valkey_raises_within_the_timeout() -> None:
    # A Valkey of the test's own, so pausing it can't stall other tests.
    with running_valkey() as server:
        client = build_client(server.url)
        cache = ValkeyCache(client)
        # Open a pooled connection first, as a running process would have.
        _ = await cache.get("k")
        container = server.container.get_wrapped_container()
        container.pause()
        try:
            started = time.monotonic()
            with pytest.raises(CacheUnavailableError) as error:
                _ = await cache.get("k")
            elapsed = time.monotonic() - started
        finally:
            container.unpause()
            await client.aclose()

    assert isinstance(error.value.__cause__, RedisTimeoutError)
    assert elapsed < TIMEOUT_SECONDS + 1


async def test_a_refused_connection_raises_cache_unavailable() -> None:
    client = build_client(f"redis://127.0.0.1:{free_port()}/0")
    cache = ValkeyCache(client)
    try:
        with pytest.raises(CacheUnavailableError) as get_error:
            _ = await cache.get("k")
        with pytest.raises(CacheUnavailableError) as set_error:
            await cache.set("k", b"v", timedelta(seconds=1))
        with pytest.raises(CacheUnavailableError) as delete_error:
            await cache.delete("k")
    finally:
        await client.aclose()

    for error in (get_error, set_error, delete_error):
        assert isinstance(error.value.__cause__, RedisConnectionError)
