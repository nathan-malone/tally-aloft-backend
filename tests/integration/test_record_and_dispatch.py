"""Recording a job and dispatching it, on real Postgres and Valkey."""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from tally_aloft.adapters.outbound.postgres.tables.jobs import JobRecord
from tally_aloft.adapters.outbound.postgres.unit_of_work import unit_of_work_factory
from tally_aloft.adapters.outbound.redis.client import build_client
from tally_aloft.adapters.outbound.taskiq.job_queue import TaskiqJobQueue
from tally_aloft.bootstrap.broker import STREAM, build_broker
from tally_aloft.bootstrap.settings import Settings
from tally_aloft.domain.shared.ids import PilotId
from tests.support.jobs import ECHO, RecordingFailedError, RecordTestJob
from tests.support.postgres import connected
from tests.support.valkey import stream_data

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from redis.asyncio import Redis
    from taskiq_redis import RedisStreamBroker

    from tally_aloft.application.shared.codec import JsonValue
    from tests.support.postgres import PostgresDatabase

pytestmark = pytest.mark.container

PAYLOAD: JsonValue = {"secret-marker": "never-in-the-message"}


@pytest.fixture
async def broker(valkey_url: str) -> AsyncGenerator[RedisStreamBroker]:
    broker = build_broker(Settings.for_tests(valkey_url=valkey_url))
    yield broker
    await broker.shutdown()


@pytest.fixture
async def valkey(valkey_url: str) -> AsyncGenerator[Redis]:
    client = build_client(valkey_url)
    yield client
    await client.aclose()


@pytest.fixture
async def use_case(
    database: PostgresDatabase, broker: RedisStreamBroker
) -> AsyncGenerator[RecordTestJob]:
    engine = create_async_engine(database.app_url)
    yield RecordTestJob(unit_of_work_factory(engine), TaskiqJobQueue(broker))
    await engine.dispose()


async def test_the_message_carries_only_the_job_id(
    use_case: RecordTestJob, broker: RedisStreamBroker, valkey: Redis
) -> None:
    job = await use_case(PilotId.new(), PAYLOAD)

    [data] = await stream_data(valkey, STREAM)
    message = broker.formatter.loads(data)

    assert message.task_name == ECHO.name
    assert message.args == [str(job.id.value)]
    assert message.kwargs == {}
    assert b"secret-marker" not in data


async def test_a_use_case_that_raises_leaves_no_row_and_no_message(
    use_case: RecordTestJob, database: PostgresDatabase, valkey: Redis
) -> None:
    with pytest.raises(RecordingFailedError):
        _ = await use_case(PilotId.new(), PAYLOAD, fail_after_record=True)

    async with connected(database.app_url) as connection:
        rows = await connection.execute(select(func.count()).select_from(JobRecord))
    assert rows.scalar_one() == 0
    assert await valkey.xlen(STREAM) == 0
