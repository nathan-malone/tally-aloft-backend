from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError

# How long the client waits to connect and for each reply, so a stalled Valkey
# raises rather than hangs.
TIMEOUT_SECONDS = 5


def build_client(url: str) -> Redis:
    """
    Return a Valkey client for `url`, which connects only when first used.

    The timeouts equal redis-py's defaults, but are set here so an upgrade
    can't change them. A connection Valkey closed, as on a restart, is retried
    once at once; a timeout never is, so a stalled Valkey raises after one.
    Replies stay `bytes`.
    """
    return Redis.from_url(
        url,
        socket_connect_timeout=TIMEOUT_SECONDS,
        socket_timeout=TIMEOUT_SECONDS,
        retry=Retry(NoBackoff(), retries=1, supported_errors=(RedisConnectionError,)),
    )
