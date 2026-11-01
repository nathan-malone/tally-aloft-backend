"""
The scheduler process from the release image: it kicks the sweeper on schedule.

It runs with a database URL whose host can't be resolved, so it would fail
if it connected to Postgres; it only needs Valkey. Kicks are read back from
the broker's stream.
"""

import time
from typing import TYPE_CHECKING

import pytest

from tally_aloft.adapters.inbound.jobs.sweeper import SWEEP_TASK
from tally_aloft.bootstrap.broker import STREAM, build_broker
from tally_aloft.bootstrap.settings import Settings
from tests.support.containers import from_container
from tests.support.in_process_worker import eventually
from tests.support.valkey import stream_data
from tests.support.worker_command import (
    SCHEDULER_MODULE,
    TEST_SCHEDULER_MODULE,
    scheduler_command,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from docker.models.containers import Container
    from redis.asyncio import Redis
    from taskiq import TaskiqMessage

pytestmark = pytest.mark.container

UNREACHABLE_DATABASE_URL = (
    "postgresql+asyncpg://unused:unused@unreachable.invalid:5432/unused"
)
# Time for the container to start Python and import the worker module.
KICK_SECONDS = 20
STOP_SECONDS = 5


@pytest.fixture
def environment(valkey_url: str) -> dict[str, str]:
    """The scheduler's settings: this run's Valkey, and no reachable database."""
    return {
        "TALLY_ENVIRONMENT": "test",
        "TALLY_DATABASE_URL": UNREACHABLE_DATABASE_URL,
        "TALLY_VALKEY_URL": from_container(valkey_url),
    }


async def _sweeps(valkey: Redis) -> list[TaskiqMessage]:
    """Return every sweeper message on the broker's stream, oldest first."""
    broker = build_broker(Settings.for_tests())
    messages = [
        broker.formatter.loads(data) for data in await stream_data(valkey, STREAM)
    ]
    await broker.shutdown()
    return [message for message in messages if message.task_name == SWEEP_TASK.name]


async def test_the_scheduler_kicks_the_sweeper_without_a_database(
    start_scheduler: Callable[[list[str], dict[str, str]], Container],
    environment: dict[str, str],
    valkey: Redis,
) -> None:
    scheduler = start_scheduler(scheduler_command(SCHEDULER_MODULE), environment)

    async def kicked() -> bool:
        return len(await _sweeps(valkey)) > 0

    await eventually(kicked, within=KICK_SECONDS)
    [first, *_] = await _sweeps(valkey)
    scheduler.reload()
    status = scheduler.status
    started = time.monotonic()
    scheduler.stop(timeout=STOP_SECONDS * 2)
    stopped_after = time.monotonic() - started

    # Labels travel as strings.
    assert float(first.labels["timeout"]) == SWEEP_TASK.timeout.total_seconds()
    assert first.args == []
    assert status == "running", scheduler.logs().decode()
    assert stopped_after < STOP_SECONDS


async def test_the_scheduler_kicks_on_the_test_schedule(
    start_scheduler: Callable[[list[str], dict[str, str]], Container],
    environment: dict[str, str],
    valkey: Redis,
) -> None:
    scheduler = start_scheduler(scheduler_command(TEST_SCHEDULER_MODULE), environment)

    async def kicked_three_times() -> bool:
        return len(await _sweeps(valkey)) >= 3

    await eventually(kicked_three_times, within=KICK_SECONDS)
    scheduler.reload()

    assert scheduler.status == "running", scheduler.logs().decode()
