from datetime import timedelta
from typing import TYPE_CHECKING

from taskiq_redis import RedisStreamBroker

from tally_aloft.adapters.outbound.redis.client import ConnectionOptions

if TYPE_CHECKING:
    from tally_aloft.bootstrap.settings import Settings

# The broker's keys sit under `broker:`, apart from the cache's `cache:` keys.
# taskiq-redis names its reclaim lock itself: `autoclaim:<group>:<stream>`.
STREAM = "broker:tasks"
CONSUMER_GROUP = "broker:workers"
# Workers and the scheduler create the group as their broker starts; the API
# never does. Starting it at the stream's first message, not at `$`, delivers
# messages the API added before the group existed.
_GROUP_START = "0"
# How long a read waits for new messages. It must stay below the connection's
# 5-second socket timeout, or every idle read would time out.
_READ_BLOCK_MILLISECONDS = 2000
# A worker takes one message per read, and reclaims one idle message after each
# new one, so it holds only what it can start, and a killed worker strands no
# backlog until `idle_timeout`.
_READ_COUNT = 1
_POOL_SIZE = 20
# taskiq-redis counts idle time in milliseconds.
_MILLISECOND = timedelta(milliseconds=1)


def build_broker(settings: Settings) -> RedisStreamBroker:
    """
    Return the Taskiq broker on Valkey, which connects only when first used.

    The stream is trimmed to about `broker_stream_maxlen` messages; a trimmed
    message's job is recovered from its row. A message left unacknowledged
    for `broker_idle_timeout` is reclaimed by the next worker that reads a new
    message. The connections share the cache client's timeouts and retry.
    """
    options = ConnectionOptions()
    return RedisStreamBroker(
        settings.valkey_url.get_secret_value(),
        queue_name=STREAM,
        consumer_group_name=CONSUMER_GROUP,
        consumer_id=_GROUP_START,
        maxlen=settings.broker_stream_maxlen,
        approximate=True,
        idle_timeout=timedelta(seconds=settings.broker_idle_timeout) // _MILLISECOND,
        xread_block=_READ_BLOCK_MILLISECONDS,
        xread_count=_READ_COUNT,
        unacknowledged_batch_size=_READ_COUNT,
        max_connection_pool_size=_POOL_SIZE,
        socket_connect_timeout=options.socket_connect_timeout,
        socket_timeout=options.socket_timeout,
        retry=options.retry,
    )


def idle_timeout(broker: RedisStreamBroker) -> timedelta:
    """Return how long a message may go unacknowledged before it is reclaimed."""
    return broker.idle_timeout * _MILLISECOND
