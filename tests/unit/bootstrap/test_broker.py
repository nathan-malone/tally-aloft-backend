from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError

from tally_aloft.adapters.outbound.redis.client import TIMEOUT_SECONDS
from tally_aloft.bootstrap.broker import CONSUMER_GROUP, STREAM, build_broker
from tally_aloft.bootstrap.settings import Settings


async def test_the_broker_reads_its_names_and_limits_from_settings() -> None:
    broker = build_broker(
        Settings.for_tests(broker_idle_timeout=12, broker_stream_maxlen=345)
    )
    try:
        assert broker.queue_name == STREAM == "broker:tasks"
        assert broker.consumer_group_name == CONSUMER_GROUP == "broker:workers"
        assert broker.idle_timeout == 12_000
        assert broker.maxlen == 345
        assert broker.approximate is True
    finally:
        await broker.shutdown()


async def test_the_group_starts_at_the_first_message() -> None:
    broker = build_broker(Settings.for_tests())
    try:
        assert broker.consumer_id == "0"
    finally:
        await broker.shutdown()


async def test_a_worker_reads_one_message_at_a_time_within_the_socket_timeout() -> None:
    broker = build_broker(Settings.for_tests())
    try:
        assert broker.count == 1
        assert broker.block < TIMEOUT_SECONDS * 1000
    finally:
        await broker.shutdown()


async def test_the_pool_shares_the_cache_clients_options() -> None:
    broker = build_broker(Settings.for_tests())
    try:
        options = broker.connection_pool.connection_kwargs
        assert broker.connection_pool.max_connections == 20
    finally:
        await broker.shutdown()

    assert options["socket_connect_timeout"] == TIMEOUT_SECONDS
    assert options["socket_timeout"] == TIMEOUT_SECONDS
    assert options["retry"] == Retry(NoBackoff(), 1, (RedisConnectionError,))
