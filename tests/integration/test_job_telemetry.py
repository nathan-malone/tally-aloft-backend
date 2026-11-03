"""Task spans and what a failing job exports, with a real worker in this process."""

from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from opentelemetry.trace import format_trace_id
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from tally_aloft.adapters.inbound.jobs.sweeper import SWEEP_TASK
from tally_aloft.adapters.outbound.postgres.tables.jobs import JobRecord
from tally_aloft.adapters.outbound.postgres.unit_of_work import unit_of_work_factory
from tally_aloft.adapters.outbound.taskiq.job_queue import TaskiqJobQueue
from tally_aloft.application.shared.jobs.kinds import UNHANDLED_ERROR_CODE
from tally_aloft.bootstrap.scrubbing import SCRUBBED_MARK, SQLSTATE_ATTRIBUTE
from tally_aloft.bootstrap.settings import Settings
from tally_aloft.bootstrap.worker import create_worker
from tally_aloft.domain.shared.ids import PilotId
from tests.support.http import request
from tests.support.in_process_worker import eventually, kick, running_worker
from tests.support.job_rows import insert_jobs
from tests.support.jobs import ECHO, TEST_IDLE_TIMEOUT, RecordTestJob
from tests.support.tasks import TEST_TASKS
from tests.support.telemetry import attributes_of
from tests.support.telemetry_jobs import UNIQUE_VIOLATION, unique_violation_task
from tests.support.telemetry_routes import PREFIX, jobs_router, telemetry_app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from opentelemetry.sdk.trace import ReadableSpan
    from sqlalchemy.ext.asyncio import AsyncEngine
    from taskiq_redis import RedisStreamBroker

    from tally_aloft.application.shared.jobs.kinds import QueuedJob
    from tests.support.postgres import PostgresDatabase
    from tests.support.telemetry import Telemetry

pytestmark = pytest.mark.container

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{TRACE_ID}-00f067aa0ba902b7-01"
JOB_MARKER = "job-marker-303"


@pytest.fixture
async def engine(database: PostgresDatabase) -> AsyncGenerator[AsyncEngine]:
    engine = create_async_engine(database.app_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def broker(
    database: PostgresDatabase,
    valkey_url: str,
    engine: AsyncEngine,
    telemetry: Telemetry,  # noqa: ARG001  # Logfire is configured before the worker runs.
) -> RedisStreamBroker:
    settings = Settings.for_tests(
        database_url=database.app_url,
        valkey_url=valkey_url,
        broker_idle_timeout=int(TEST_IDLE_TIMEOUT.total_seconds()),
    )
    return create_worker(settings, [*TEST_TASKS, unique_violation_task(engine)])


def spans_named(telemetry: Telemetry, name: str) -> list[ReadableSpan]:
    return [span for span in telemetry.spans() if span.name == name]


def trace_id(span: ReadableSpan) -> str:
    assert span.context is not None
    return format_trace_id(span.context.trace_id)


async def test_a_jobs_span_continues_the_trace_of_the_request_that_recorded_it(
    engine: AsyncEngine, broker: RedisStreamBroker, telemetry: Telemetry
) -> None:
    app = telemetry_app(jobs_router(engine, broker))

    async def echoed() -> bool:
        return len(spans_named(telemetry, ECHO.name)) > 0

    async with running_worker(broker):
        response = await request(
            app,
            "POST",
            f"{PREFIX}/jobs",
            headers={"traceparent": TRACEPARENT},
            json={"marker": "echo"},
        )
        assert response.status_code == HTTPStatus.ACCEPTED
        await eventually(echoed)

    [job_span] = spans_named(telemetry, ECHO.name)
    assert trace_id(job_span) == TRACE_ID


async def test_a_job_the_sweeper_dispatched_continues_the_sweeps_trace(
    engine: AsyncEngine, broker: RedisStreamBroker, telemetry: Telemetry
) -> None:
    _ = await insert_jobs(engine, ECHO, dispatched_ago=timedelta(minutes=10))

    async def echoed() -> bool:
        return len(spans_named(telemetry, ECHO.name)) > 0

    async with running_worker(broker):
        await kick(broker, SWEEP_TASK.name)
        await eventually(echoed)

    [sweep_span] = spans_named(telemetry, SWEEP_TASK.name)
    [job_span] = spans_named(telemetry, ECHO.name)
    assert trace_id(job_span) == trace_id(sweep_span)


async def test_a_jobs_database_error_leaves_no_trace_of_its_data(
    engine: AsyncEngine, broker: RedisStreamBroker, telemetry: Telemetry
) -> None:
    record = RecordTestJob(unit_of_work_factory(engine), TaskiqJobQueue(broker))
    job = await record(PilotId.new(), JOB_MARKER, kind=UNIQUE_VIOLATION)

    async def failed() -> bool:
        return await _error_code(engine, job) == UNHANDLED_ERROR_CODE

    async with running_worker(broker):
        await eventually(failed)

    telemetry.assert_absent(JOB_MARKER)
    [task_span] = spans_named(telemetry, UNIQUE_VIOLATION.name)
    assert task_span.context is not None
    [log_event] = [
        event
        for span in telemetry.spans()
        if "raised" in str(attributes_of(span).get("logfire.msg"))
        for event in span.events
        if event.name == "exception"
    ]
    assert attributes_of(log_event)[SQLSTATE_ATTRIBUTE] == "23505"
    assert attributes_of(log_event)[SCRUBBED_MARK] is True
    assert "SQLSTATE 23505" in telemetry.logs()


async def _error_code(engine: AsyncEngine, job: QueuedJob) -> str | None:
    async with engine.connect() as connection:
        result = await connection.execute(
            select(JobRecord.error_code).where(JobRecord.id == job.id.value)
        )
    code: object = result.scalar_one()
    return code if isinstance(code, str) else None
