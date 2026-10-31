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

from tally_aloft.adapters.inbound.jobs.tasks import TaskDefinition
from tally_aloft.application.registry import UseCases
from tally_aloft.bootstrap.broker import CONSUMER_GROUP, STREAM, build_broker
from tally_aloft.bootstrap.settings import Settings
from tally_aloft.bootstrap.worker import create_worker
from tests.support import tasks
from tests.support.in_process_worker import eventually, kick, running_worker
from tests.support.jobs import TEST_IDLE_TIMEOUT
from tests.support.valkey import count, pending

if TYPE_CHECKING:
    from tests.support.postgres import PostgresDatabase

pytestmark = pytest.mark.container


@pytest.fixture
def settings(database: PostgresDatabase, valkey_url: str) -> Settings:
    return Settings.for_tests(
        database_url=database.app_url,
        valkey_url=valkey_url,
        broker_idle_timeout=int(TEST_IDLE_TIMEOUT.total_seconds()),
    )


@pytest.mark.parametrize("fail", [False, True], ids=["returns", "raises"])
async def test_a_task_is_acknowledged_once_it_ends(
    settings: Settings, valkey: Redis, *, fail: bool
) -> None:
    broker = create_worker(settings, tasks.TEST_TASKS)
    key = uuid.uuid4().hex

    async with running_worker(broker):
        await kick(broker, tasks.RECORD, key=key, fail=fail)
        await eventually(lambda: _ran(valkey, tasks.runs_key(key)))
        await eventually(lambda: _nonepending(valkey))

    assert await count(valkey, tasks.runs_key(key)) == 1


async def _ran(valkey: Redis, key: str) -> bool:
    return await count(valkey, key) == 1


async def _nonepending(valkey: Redis) -> bool:
    return await pending(valkey) == 0


async def test_the_stream_and_group_are_the_brokers_own(
    settings: Settings, valkey: Redis
) -> None:
    broker = create_worker(settings, tasks.TEST_TASKS)

    async with running_worker(broker):
        await kick(broker, tasks.RECORD, key="k", fail=False)
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
        await kick(broker, tasks.USE_CASES)
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
        await kick(broker, tasks.SLEEP, key=key, seconds=30)
        await eventually(lambda: _ran(valkey, tasks.started_key(key)))
        await eventually(lambda: _nonepending(valkey), within=5)

    assert await count(valkey, tasks.finished_key(key)) == 0


async def test_the_stream_is_trimmed_to_about_maxlen(
    valkey_url: str, valkey: Redis
) -> None:
    broker = build_broker(
        Settings.for_tests(valkey_url=valkey_url, broker_stream_maxlen=100)
    )
    try:
        for _ in range(1000):
            await kick(broker, tasks.RECORD, key="k", fail=False)
    finally:
        await broker.shutdown()

    assert await valkey.xlen(STREAM) <= 300
