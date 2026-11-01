"""Real Valkey for the tests, in a testcontainers container."""

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from testcontainers.community.valkey import ValkeyContainer

from tally_aloft.adapters.outbound.redis.client import build_client
from tally_aloft.bootstrap.broker import CONSUMER_GROUP, STREAM
from tests.support.containers import started

if TYPE_CHECKING:
    from collections.abc import Generator

    from redis.asyncio import Redis
    from taskiq import AsyncBroker, TaskiqMessage

# The one place the tests' Valkey version is set; compose.yaml must match it.
VALKEY_IMAGE = "valkey/valkey:9"


@dataclass(frozen=True, slots=True)
class ValkeyServer:
    """A running test Valkey: its URL, and its container for tests that pause it."""

    url: str
    container: ValkeyContainer


@contextmanager
def running_valkey() -> Generator[ValkeyServer]:
    """Start Valkey, and stop it on exit."""
    with started(lambda: ValkeyContainer(VALKEY_IMAGE)) as container:
        # redis-py doesn't accept the container's own `valkey://` URL.
        url = f"redis://{container.get_host()}:{container.get_exposed_port()}/0"
        yield ValkeyServer(url=url, container=container)


async def flush(url: str) -> None:
    """Delete every key in the database at `url`."""
    client = build_client(url)
    try:
        _ = await client.flushdb()
    finally:
        await client.aclose()


async def stream_data(client: Redis, stream: str) -> list[bytes]:
    """Return the `data` field of each entry in `stream`, oldest first."""
    entries = cast(
        "list[tuple[bytes, dict[bytes, bytes]]]", await client.xrange(stream)
    )
    return [fields[b"data"] for _, fields in entries]


async def stream_messages(
    client: Redis, broker: AsyncBroker, stream: str = STREAM
) -> list[TaskiqMessage]:
    """Return each message in `stream`, oldest first, decoded as `broker` does."""
    data = await stream_data(client, stream)
    return [broker.formatter.loads(entry) for entry in data]


async def count(client: Redis, key: str) -> int:
    """Return the counter at `key`, or 0 if it was never incremented."""
    value = await client.get(key)
    return 0 if value is None else int(value)


async def pending(client: Redis) -> int:
    """Return how many of the broker's messages are delivered but unacknowledged."""
    summary = await client.xpending(STREAM, CONSUMER_GROUP)
    return int(summary["pending"])
