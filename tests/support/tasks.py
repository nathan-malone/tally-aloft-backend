"""
Test-only Taskiq tasks, which the test worker registers.

The probe tasks exercise the broker itself: acknowledgement, redelivery and
shutdown. They count their runs in Valkey, under `probe:` keys, through the
broker's own connection pool. This module runs inside the release image, with
`tests/` mounted, so it imports only the runtime packages.
"""

import asyncio
from datetime import timedelta
from typing import Annotated

from redis.asyncio import Redis
from taskiq import Context, TaskiqDepends
from taskiq_redis import RedisStreamBroker

from tally_aloft.adapters.inbound.jobs.dependencies import UseCasesDep  # noqa: TC001  # Taskiq reads task annotations at run time.
from tally_aloft.adapters.inbound.jobs.tasks import TaskDefinition
from tally_aloft.application.registry import UseCases  # noqa: TC001  # Taskiq reads task annotations at run time.
from tests.support.jobs import TEST_JOB_TASKS

RECORD = "probe.record"
SLEEP = "probe.sleep"
USE_CASES = "probe.use_cases"
# Under the test idle_timeout of 10 seconds, with room for a 5-second sleep.
PROBE_TIMEOUT = timedelta(seconds=8)

# The `UseCases` each `probe.use_cases` run received, for in-process tests.
received_use_cases: list[UseCases] = []

ContextDep = Annotated[Context, TaskiqDepends()]


def runs_key(key: str) -> str:
    """Return the counter `probe.record` increments for `key`."""
    return f"probe:runs:{key}"


def started_key(key: str) -> str:
    """Return the counter `probe.sleep` increments when it starts for `key`."""
    return f"probe:started:{key}"


def finished_key(key: str) -> str:
    """Return the counter `probe.sleep` increments when it finishes for `key`."""
    return f"probe:finished:{key}"


def _valkey(context: Context) -> Redis:
    """Return a client on the broker's own connection pool."""
    broker = context.broker
    if not isinstance(broker, RedisStreamBroker):
        msg = "the probe tasks run only on the stream broker"
        raise TypeError(msg)
    return Redis(connection_pool=broker.connection_pool)


async def record(key: str, fail: bool, context: ContextDep) -> None:  # noqa: FBT001  # Taskiq passes message arguments by name.
    """Count one run for `key`, then raise if `fail`."""
    async with _valkey(context) as client:
        _ = await client.incr(runs_key(key))
    if fail:
        msg = "probe failure"
        raise RuntimeError(msg)


async def sleep(key: str, seconds: float, context: ContextDep) -> None:
    """Count a start, sleep `seconds` on the first delivery only, count a finish."""
    async with _valkey(context) as client:
        starts = await client.incr(started_key(key))
    if starts == 1:
        await asyncio.sleep(seconds)
    async with _valkey(context) as client:
        _ = await client.incr(finished_key(key))


async def use_cases(use_cases: UseCasesDep) -> None:
    """Keep the `UseCases` this run received."""
    received_use_cases.append(use_cases)


PROBE_TASKS = (
    TaskDefinition(name=RECORD, function=record, timeout=PROBE_TIMEOUT),
    TaskDefinition(name=SLEEP, function=sleep, timeout=PROBE_TIMEOUT),
    TaskDefinition(name=USE_CASES, function=use_cases, timeout=PROBE_TIMEOUT),
)

# Every task the test worker registers.
TEST_TASKS: tuple[TaskDefinition, ...] = (*PROBE_TASKS, *TEST_JOB_TASKS)
