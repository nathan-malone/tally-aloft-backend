"""
The broker and the worker against real Valkey, with a worker in this process.

Each test builds the test worker's broker with `create_worker`, as
`tests/support/worker.py` does, and runs Taskiq's own receiver on it.
"""

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine
from taskiq.kicker import AsyncKicker

from tally_aloft.adapters.inbound.jobs.tasks import TaskDefinition
from tally_aloft.adapters.outbound.redis.client import build_client
from tally_aloft.application.registry import UseCases
from tally_aloft.bootstrap.broker import CONSUMER_GROUP, STREAM, build_broker
from tally_aloft.bootstrap.settings import Settings
from tally_aloft.bootstrap.worker import create_worker
from tests.support import tasks
from tests.support.in_process_worker import eventually, running_worker

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from taskiq_redis import RedisStreamBroker

    from tests.support.postgres import PostgresDatabase

pytestmark = pytest.mark.container

# The test worker's idle_timeout, as in the image suite.
IDLE_TIMEOUT = 10


@pytest.fixture
def settings(database: PostgresDatabase, valkey_url: str) -> Settings:
    return Settings.for_tests(
        database_url=database.app_url,
        valkey_url=valkey_url,
        broker_idle_timeout=IDLE_TIMEOUT,
    )


@pytest.fixture
async def valkey(valkey_url: str) -> AsyncGenerator[Redis]:
    """A client for reading the broker's keys and the probes' counters."""
    client = build_client(valkey_url)
    yield client
    await client.aclose()


async def _kick(broker: RedisStreamBroker, task_name: str, **kwargs: object) -> None:
    """Kick `task_name` by name, as the job queue does."""
    _ = await AsyncKicker(task_name, broker, {}).kiq(**kwargs)


async def _count(valkey: Redis, key: str) -> int:
    value = await valkey.get(key)
    return 0 if value is None else int(value)


async def _pending(valkey: Redis) -> int:
    summary = await valkey.xpending(STREAM, CONSUMER_GROUP)
    return int(summary["pending"])


@pytest.mark.parametrize("fail", [False, True], ids=["returns", "raises"])
async def test_a_task_is_acknowledged_once_it_ends(
    settings: Settings, valkey: Redis, *, fail: bool
) -> None:
    broker = create_worker(settings, tasks.TEST_TASKS)
    key = uuid.uuid4().hex

    async with running_worker(broker):
        await _kick(broker, tasks.RECORD, key=key, fail=fail)
        await eventually(lambda: _ran(valkey, tasks.runs_key(key)))
        await eventually(lambda: _none_pending(valkey))

    assert await _count(valkey, tasks.runs_key(key)) == 1


async def _ran(valkey: Redis, key: str) -> bool:
    return await _count(valkey, key) == 1


async def _none_pending(valkey: Redis) -> bool:
    return await _pending(valkey) == 0


async def test_the_stream_and_group_are_the_brokers_own(
    settings: Settings, valkey: Redis
) -> None:
    broker = create_worker(settings, tasks.TEST_TASKS)

    async with running_worker(broker):
        await _kick(broker, tasks.RECORD, key="k", fail=False)
        await eventually(lambda: _ran(valkey, tasks.runs_key("k")))

    groups = await valkey.xinfo_groups(STREAM)
    assert [group["name"] for group in groups] == [CONSUMER_GROUP.encode()]


async def test_a_task_gets_the_use_cases_and_shutdown_closes_the_resources(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    disposed: list[AsyncEngine] = []
    closed: list[Redis] = []

    async def dispose(self: AsyncEngine, *, close: bool = True) -> None:  # noqa: ARG001  # It mirrors dispose's signature.
        disposed.append(self)

    async def aclose(self: Redis, close_connection_pool: bool | None = None) -> None:  # noqa: ARG001, FBT001  # It mirrors aclose's signature.
        closed.append(self)

    monkeypatch.setattr(AsyncEngine, "dispose", dispose)
    monkeypatch.setattr(Redis, "aclose", aclose)
    tasks.received_use_cases.clear()
    broker = create_worker(settings, tasks.TEST_TASKS)

    async with running_worker(broker):
        await _kick(broker, tasks.USE_CASES)
        await eventually(_received_use_cases)
        assert disposed == []

    assert isinstance(tasks.received_use_cases[0], UseCases)
    assert len(disposed) == 1
    # Taskiq closes clients on the broker's own pool as it reads and kicks.
    cache_clients = [
        c for c in closed if c.connection_pool is not broker.connection_pool
    ]
    assert len(cache_clients) == 1


async def _received_use_cases() -> bool:
    return len(tasks.received_use_cases) == 1


async def test_a_task_kicked_by_name_is_stopped_by_its_registered_timeout(
    settings: Settings, valkey: Redis
) -> None:
    short = TaskDefinition(
        name=tasks.SLEEP, function=tasks.sleep, timeout=timedelta(seconds=1)
    )
    broker = create_worker(settings, [short])
    key = uuid.uuid4().hex

    async with running_worker(broker):
        await _kick(broker, tasks.SLEEP, key=key, seconds=30)
        await eventually(lambda: _ran(valkey, tasks.started_key(key)))
        await eventually(lambda: _none_pending(valkey), within=5)

    assert await _count(valkey, tasks.finished_key(key)) == 0


async def test_the_stream_is_trimmed_to_about_maxlen(
    valkey_url: str, valkey: Redis
) -> None:
    broker = build_broker(
        Settings.for_tests(valkey_url=valkey_url, broker_stream_maxlen=100)
    )
    try:
        for _ in range(1000):
            await _kick(broker, tasks.RECORD, key="k", fail=False)
    finally:
        await broker.shutdown()

    assert await valkey.xlen(STREAM) <= 300
