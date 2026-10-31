from dataclasses import dataclass, field

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError

# How long the client waits to connect and for each reply, so a stalled Valkey
# raises rather than hangs.
TIMEOUT_SECONDS = 5


def _retry_a_dropped_connection_once() -> Retry:
    return Retry(NoBackoff(), retries=1, supported_errors=(RedisConnectionError,))


@dataclass(frozen=True, slots=True)
class ConnectionOptions:
    """
    The connection options every Valkey user in a process shares.

    The timeouts equal redis-py's defaults, but are set here so an upgrade
    can't change them. A connection Valkey closed, as on a restart, is retried
    once at once; a timeout never is, so a stalled Valkey raises after one.
    The cache's client and the broker's connection pool both take them.
    """

    socket_connect_timeout: float = TIMEOUT_SECONDS
    socket_timeout: float = TIMEOUT_SECONDS
    retry: Retry = field(default_factory=_retry_a_dropped_connection_once)


def build_client(url: str) -> Redis:
    """Return a Valkey client for `url`, which connects only when first used."""
    options = ConnectionOptions()
    # Replies stay `bytes`: decode_responses is left off.
    return Redis.from_url(
        url,
        socket_connect_timeout=options.socket_connect_timeout,
        socket_timeout=options.socket_timeout,
        retry=options.retry,
    )
