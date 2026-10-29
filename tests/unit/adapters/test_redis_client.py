from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError

from tally_aloft.adapters.outbound.redis.client import TIMEOUT_SECONDS, build_client
from tally_aloft.bootstrap.settings import UNUSED_VALKEY_URL


async def test_the_client_times_out_and_retries_only_a_dropped_connection() -> None:
    client = build_client(UNUSED_VALKEY_URL)
    try:
        options = client.connection_pool.connection_kwargs
    finally:
        await client.aclose()

    assert options["socket_connect_timeout"] == TIMEOUT_SECONDS
    assert options["socket_timeout"] == TIMEOUT_SECONDS
    assert options["retry"] == Retry(NoBackoff(), 1, (RedisConnectionError,))
    assert options.get("decode_responses", False) is False
