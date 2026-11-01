"""The `JobQueue` port: the fake against `TaskiqJobQueue` on real Valkey."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from uuid import uuid7

import pytest

from tally_aloft.adapters.outbound.redis.client import build_client
from tally_aloft.adapters.outbound.taskiq.job_queue import TaskiqJobQueue
from tally_aloft.application.shared.jobs.kinds import JobId
from tally_aloft.bootstrap.broker import build_broker
from tally_aloft.bootstrap.settings import Settings
from tests.fakes.job_queue import FakeJobQueue
from tests.support.valkey import stream_messages

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from tally_aloft.application.ports.job_queue import JobQueue


@dataclass(frozen=True, slots=True)
class QueueCase:
    """A job queue under test, and how to read back what reached it."""

    queue: JobQueue
    # Each dispatch that arrived, as the job ID's text and the task's name.
    arrived: Callable[[], Awaitable[list[tuple[str, str]]]]


@pytest.fixture
def fake_case() -> QueueCase:
    queue = FakeJobQueue()

    async def arrived() -> list[tuple[str, str]]:
        return [(str(job_id.value), name) for job_id, name in queue.dispatched]

    return QueueCase(queue, arrived)


@pytest.fixture
async def taskiq_case(valkey_url: str) -> AsyncGenerator[QueueCase]:
    broker = build_broker(Settings.for_tests(valkey_url=valkey_url))
    client = build_client(valkey_url)

    async def arrived() -> list[tuple[str, str]]:
        messages = await stream_messages(client, broker)
        return [(str(message.args[0]), message.task_name) for message in messages]

    yield QueueCase(TaskiqJobQueue(broker), arrived)
    await client.aclose()
    await broker.shutdown()


@pytest.fixture(
    params=["fake_case", pytest.param("taskiq_case", marks=pytest.mark.container)]
)
def case(request: pytest.FixtureRequest) -> QueueCase:
    return cast("QueueCase", request.getfixturevalue(cast("str", request.param)))


async def test_a_dispatched_id_arrives_under_its_kind_name(case: QueueCase) -> None:
    job_id = JobId(uuid7())

    await case.queue.dispatch(job_id, "test.kind")

    assert await case.arrived() == [(str(job_id.value), "test.kind")]


async def test_dispatches_arrive_in_order(case: QueueCase) -> None:
    first, second = JobId(uuid7()), JobId(uuid7())

    await case.queue.dispatch(first, "test.a")
    await case.queue.dispatch(second, "test.b")

    assert await case.arrived() == [
        (str(first.value), "test.a"),
        (str(second.value), "test.b"),
    ]
