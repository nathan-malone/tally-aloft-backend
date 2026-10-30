import asyncio
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from taskiq.receiver import Receiver

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from taskiq_redis import RedisStreamBroker

# How long a test waits for a worker to do something before it fails.
WAIT_SECONDS = 20
_POLL_SECONDS = 0.05
# As the production worker command's `--max-async-tasks`.
_MAX_ASYNC_TASKS = 10


@asynccontextmanager
async def running_worker(broker: RedisStreamBroker) -> AsyncGenerator[None]:
    """
    Run a Taskiq worker on `broker` in this event loop, until the block exits.

    It is the receiver `taskiq worker` runs in each process, so startup,
    acknowledgement and shutdown are the real ones, without a subprocess.
    """
    broker.is_worker_process = True
    receiver = Receiver(
        broker, max_async_tasks=_MAX_ASYNC_TASKS, wait_tasks_timeout=WAIT_SECONDS
    )
    finish = asyncio.Event()
    listening = asyncio.create_task(receiver.listen(finish))
    try:
        yield
    finally:
        finish.set()
        try:
            await listening
        finally:
            await broker.shutdown()


async def eventually(
    check: Callable[[], Awaitable[bool]], within: float = WAIT_SECONDS
) -> None:
    """Poll `check` until it returns True, or fail after `within` seconds."""
    deadline = time.monotonic() + within
    while not await check():
        if time.monotonic() > deadline:
            msg = f"the condition didn't hold within {within} seconds"
            raise AssertionError(msg)
        await asyncio.sleep(_POLL_SECONDS)
