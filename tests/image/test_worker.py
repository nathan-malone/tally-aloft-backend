"""
The worker process from the release image: redelivery, shutdown and bad starts.

Each worker runs `taskiq worker` on the test worker module, with `tests/`
mounted, against this run's Postgres and Valkey. These tests settle how the
broker really behaves (R-7), at the message level, with the probe tasks.
"""

import asyncio
import time
import uuid
from typing import TYPE_CHECKING

import pytest
from redis.exceptions import ResponseError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from tally_aloft.adapters.outbound.postgres.tables.jobs import JobRecord
from tally_aloft.adapters.outbound.postgres.unit_of_work import unit_of_work_factory
from tally_aloft.adapters.outbound.taskiq.job_queue import TaskiqJobQueue
from tally_aloft.application.shared.jobs.kinds import JobStatus
from tally_aloft.bootstrap.broker import CONSUMER_GROUP, STREAM, build_broker
from tally_aloft.bootstrap.settings import Settings
from tally_aloft.domain.shared.ids import PilotId
from tests.support import tasks
from tests.support.containers import from_container
from tests.support.in_process_worker import eventually, kick
from tests.support.jobs import SLOW_FIRST_ATTEMPT, TEST_IDLE_TIMEOUT, RecordTestJob
from tests.support.postgres import connected
from tests.support.server import REQUIRED_SETTINGS
from tests.support.valkey import count, pending
from tests.support.worker_command import TEST_WORKER_MODULE, worker_command

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from docker.models.containers import Container
    from redis.asyncio import Redis
    from taskiq_redis import RedisStreamBroker

    from tally_aloft.application.shared.jobs.kinds import QueuedJob
    from tests.support.postgres import PostgresDatabase

pytestmark = pytest.mark.container

# The image contract's worker command on the test worker module, in one
# worker process so a message has nowhere else to go.
WORKER_COMMAND = [*worker_command(TEST_WORKER_MODULE), "--workers", "1"]
# A listening worker tries a read at least every 2 seconds (the broker's read
# block), so a consumer whose `idle` time, since its last attempted read,
# passes this has stopped fetching. The
# workers' own INFO logs can't say so: Python 3.14 starts worker processes with
# `forkserver`, and Taskiq configures their logging only under `spawn`.
STOPPED_READING_MILLISECONDS = 3000
# Time for a worker to reclaim a message: the idle timeout plus margin.
REDELIVERY_SECONDS = 40
EXIT_SECONDS = 30


@pytest.fixture
def environment(database: PostgresDatabase, valkey_url: str) -> dict[str, str]:
    """The test worker's settings, reaching this run's data stores."""
    return {
        "TALLY_ENVIRONMENT": "test",
        "TALLY_DATABASE_URL": from_container(database.app_url),
        "TALLY_VALKEY_URL": from_container(valkey_url),
        "TALLY_BROKER_IDLE_TIMEOUT": str(int(TEST_IDLE_TIMEOUT.total_seconds())),
    }


@pytest.fixture
async def kicker(valkey_url: str) -> AsyncGenerator[RedisStreamBroker]:
    """A broker on this run's Valkey, for kicking tasks as the API does."""
    broker = build_broker(Settings.for_tests(valkey_url=valkey_url))
    yield broker
    await broker.shutdown()


async def _consumers(valkey: Redis) -> dict[str, int]:
    """Return each consumer in the group, with milliseconds since its last read."""
    try:
        rows = await valkey.xinfo_consumers(STREAM, CONSUMER_GROUP)
    except ResponseError:
        return {}
    return {str(row["name"]): int(row["idle"]) for row in rows}


async def _started(
    start: Callable[[], Container], valkey: Redis
) -> tuple[Container, str]:
    """Start a worker, wait until it reads, and return it and its consumer name."""
    before = set(await _consumers(valkey))
    worker = start()
    joined: list[str] = []

    async def reading() -> bool:
        joined.extend(set(await _consumers(valkey)) - before)
        return len(joined) > 0

    await eventually(reading)
    return worker, joined[0]


async def _stopped_fetching(worker: Container, valkey: Redis, consumer: str) -> None:
    """Wait until `worker` has exited or its consumer has stopped reading."""

    async def stopped() -> bool:
        worker.reload()
        if worker.status == "exited":
            return True
        idle = (await _consumers(valkey)).get(consumer, 0)
        return idle > STOPPED_READING_MILLISECONDS

    await eventually(stopped)


async def _until_redelivered(
    kicker: RedisStreamBroker, done: Callable[[], Awaitable[bool]]
) -> None:
    """
    Kick a fresh message every second until `done` holds.

    taskiq-redis reclaims an idle message only after a read returns a new
    one, so the fresh messages are what let the running worker reclaim it.
    """

    async def check() -> bool:
        if await done():
            return True
        await kick(kicker, tasks.RECORD, key="nudge", fail=False)
        await asyncio.sleep(1)
        return False

    await eventually(check, within=REDELIVERY_SECONDS)


async def _finish_by_redelivery(
    kicker: RedisStreamBroker, valkey: Redis, key: str
) -> None:
    """Redeliver until `probe.sleep` for `key` finishes."""

    async def finished() -> bool:
        return await count(valkey, tasks.finished_key(key)) > 0

    await _until_redelivered(kicker, finished)


async def _job_is(
    database: PostgresDatabase, job: QueuedJob, status: str, attempts: int
) -> bool:
    async with connected(database.app_url) as connection:
        result = await connection.execute(
            select(JobRecord.status, JobRecord.attempts).where(
                JobRecord.id == job.id.value
            )
        )
        row = result.one()
    return (row.status, row.attempts) == (status, attempts)


def test_the_image_imports_the_worker_with_only_the_sweeper(
    start_worker: Callable[[list[str], dict[str, str]], Container],
) -> None:
    command = [
        "python",
        "-c",
        (
            "import tally_aloft.worker as w; "
            "print(*sorted(w.broker.get_all_tasks()), type(w.scheduler).__name__)"
        ),
    ]
    container = start_worker(
        command,
        REQUIRED_SETTINGS,
    )

    result = container.wait(timeout=EXIT_SECONDS)

    assert result["StatusCode"] == 0, container.logs().decode()
    output = container.logs(stdout=True, stderr=False).decode().strip()
    assert output == "idempotency.cleanup jobs.sweep TaskiqScheduler"


async def test_a_killed_workers_message_is_redelivered(
    start_worker: Callable[[list[str], dict[str, str]], Container],
    environment: dict[str, str],
    kicker: RedisStreamBroker,
    valkey: Redis,
) -> None:
    key = uuid.uuid4().hex
    first, _ = await _started(lambda: start_worker(WORKER_COMMAND, environment), valkey)
    await kick(kicker, tasks.SLEEP, key=key, seconds=6)
    await eventually(lambda: _counted(valkey, tasks.started_key(key), 1))

    first.kill()
    await asyncio.to_thread(first.wait, timeout=EXIT_SECONDS)
    assert await pending(valkey) == 1
    _ = await _started(lambda: start_worker(WORKER_COMMAND, environment), valkey)
    await _finish_by_redelivery(kicker, valkey, key)

    assert await count(valkey, tasks.started_key(key)) == 2
    assert await count(valkey, tasks.finished_key(key)) == 1


async def _counted(valkey: Redis, key: str, expected: int) -> bool:
    return await count(valkey, key) == expected


async def test_sigterm_stops_fetching_and_finishes_the_running_task(
    start_worker: Callable[[list[str], dict[str, str]], Container],
    environment: dict[str, str],
    kicker: RedisStreamBroker,
    valkey: Redis,
) -> None:
    key = uuid.uuid4().hex
    worker, consumer = await _started(
        lambda: start_worker(WORKER_COMMAND, environment), valkey
    )
    await kick(kicker, tasks.SLEEP, key=key, seconds=3)
    await eventually(lambda: _counted(valkey, tasks.started_key(key), 1))

    worker.kill(signal="SIGTERM")
    await _stopped_fetching(worker, valkey, consumer)
    await kick(kicker, tasks.RECORD, key=key, fail=False)
    result = await asyncio.to_thread(worker.wait, timeout=EXIT_SECONDS)

    assert result["StatusCode"] == 0, worker.logs().decode()
    assert await count(valkey, tasks.finished_key(key)) == 1
    assert await count(valkey, tasks.runs_key(key)) == 0
    assert await pending(valkey) == 0


async def test_sigterm_leaves_a_task_still_running_unacknowledged(
    start_worker: Callable[[list[str], dict[str, str]], Container],
    environment: dict[str, str],
    kicker: RedisStreamBroker,
    valkey: Redis,
) -> None:
    key = uuid.uuid4().hex
    sleep_seconds = 6
    worker, consumer = await _started(
        lambda: start_worker(
            [
                *worker_command(TEST_WORKER_MODULE, wait_tasks_seconds=1),
                "--workers",
                "1",
            ],
            environment,
        ),
        valkey,
    )
    await kick(kicker, tasks.SLEEP, key=key, seconds=sleep_seconds)
    await eventually(lambda: _counted(valkey, tasks.started_key(key), 1))

    signalled = time.monotonic()
    worker.kill(signal="SIGTERM")
    await _stopped_fetching(worker, valkey, consumer)
    await kick(kicker, tasks.RECORD, key=key, fail=False)
    _ = await asyncio.to_thread(worker.wait, timeout=EXIT_SECONDS)
    elapsed = time.monotonic() - signalled

    assert elapsed < sleep_seconds
    assert await count(valkey, tasks.finished_key(key)) == 0
    assert await count(valkey, tasks.runs_key(key)) == 0
    assert await pending(valkey) == 1
    _ = await _started(lambda: start_worker(WORKER_COMMAND, environment), valkey)
    await _finish_by_redelivery(kicker, valkey, key)
    await eventually(lambda: _counted(valkey, tasks.runs_key(key), 1))


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"TALLY_DATABASE_URL": None}, id="missing-setting"),
        pytest.param({"TALLY_BROKER_IDLE_TIMEOUT": "5"}, id="idle-timeout-too-short"),
        pytest.param(
            {"TALLY_DATABASE_STATEMENT_TIMEOUT": "31"},
            id="statement-timeout-past-the-backstop",
        ),
    ],
)
def test_a_worker_that_cannot_start_exits_non_zero(
    start_worker: Callable[[list[str], dict[str, str]], Container],
    environment: dict[str, str],
    change: dict[str, str | None],
) -> None:
    changed = {**environment, **change}
    settings = {name: value for name, value in changed.items() if value is not None}

    worker = start_worker(WORKER_COMMAND, settings)
    result = worker.wait(timeout=EXIT_SECONDS)

    assert result["StatusCode"] != 0, worker.logs().decode()


async def test_a_killed_workers_job_finishes_once_on_redelivery(
    start_worker: Callable[[list[str], dict[str, str]], Container],
    environment: dict[str, str],
    kicker: RedisStreamBroker,
    valkey: Redis,
    database: PostgresDatabase,
) -> None:
    engine = create_async_engine(database.app_url)
    record = RecordTestJob(unit_of_work_factory(engine), TaskiqJobQueue(kicker))
    try:
        first, _ = await _started(
            lambda: start_worker(WORKER_COMMAND, environment), valkey
        )
        job = await record(PilotId.new(), 5, kind=SLOW_FIRST_ATTEMPT)
        await eventually(lambda: _job_is(database, job, JobStatus.RUNNING, 1))

        first.kill()
        await asyncio.to_thread(first.wait, timeout=EXIT_SECONDS)
        _ = await _started(lambda: start_worker(WORKER_COMMAND, environment), valkey)
        await _until_redelivered(
            kicker, lambda: _job_is(database, job, JobStatus.SUCCEEDED, 2)
        )
    finally:
        await engine.dispose()
