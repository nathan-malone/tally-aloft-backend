from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from taskiq import TaskiqMessage, TaskiqState

from tally_aloft.adapters.inbound.jobs.dependencies import get_use_cases
from tally_aloft.adapters.inbound.jobs.labels import RegisteredLabelsMiddleware
from tally_aloft.adapters.inbound.jobs.tasks import (
    JOB_BACKSTOP,
    TaskDefinition,
    job_task,
)
from tally_aloft.adapters.outbound.postgres.engine import (
    CONNECT_TIMEOUT_SECONDS,
    POOL_TIMEOUT_SECONDS,
)
from tally_aloft.application.shared.jobs.kinds import JobKind
from tally_aloft.bootstrap.broker import build_broker
from tally_aloft.bootstrap.container import register_tasks
from tally_aloft.bootstrap.settings import Settings
from tests.support.codecs import JSON

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from taskiq_redis import RedisStreamBroker

    from tally_aloft.application.registry import UseCases
    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.jobs.kinds import ClaimedJob, JobBody

KIND = JobKind(
    name="test.wiring",
    payload_codec=JSON,
    result_codec=JSON,
    time_limit=timedelta(seconds=40),
)


async def _noop() -> None:
    return None


async def _body(claimed: ClaimedJob[JsonValue]) -> JsonValue:
    return claimed.payload


def _pick_body(_use_cases: UseCases) -> JobBody[JsonValue, JsonValue]:
    return _body


def _message(task_name: str, labels: dict[str, object]) -> TaskiqMessage:
    return TaskiqMessage(
        task_id="id",
        task_name=task_name,
        labels=labels,
        labels_types={},
        args=[],
        kwargs={},
    )


@pytest.fixture
async def broker() -> AsyncGenerator[RedisStreamBroker]:
    """A broker with one task, `task`, registered with a 3-second timeout."""
    broker = build_broker(Settings.for_tests())
    register_tasks(
        broker,
        [TaskDefinition(name="task", function=_noop, timeout=timedelta(seconds=3))],
    )
    yield broker
    await broker.shutdown()


@pytest.fixture
def middleware(broker: RedisStreamBroker) -> RegisteredLabelsMiddleware:
    middleware = RegisteredLabelsMiddleware()
    middleware.set_broker(broker)
    return middleware


def test_a_job_task_runs_its_kind_under_its_name() -> None:
    definition = job_task(KIND, _pick_body)

    assert (definition.name, definition.kind_name) == (KIND.name, KIND.name)


def test_a_job_tasks_timeout_is_its_time_limit_plus_the_backstop() -> None:
    assert job_task(KIND, _pick_body).timeout == KIND.time_limit + JOB_BACKSTOP


def test_the_backstop_covers_the_claim_and_the_outcomes_write() -> None:
    # Each waits for a pooled connection, then to connect, then runs up to the
    # default statement timeout.
    statement_timeout = Settings.for_tests().database_statement_timeout
    step = POOL_TIMEOUT_SECONDS + CONNECT_TIMEOUT_SECONDS + statement_timeout

    assert timedelta(seconds=2 * step) < JOB_BACKSTOP


async def test_a_default_job_kind_fits_the_default_idle_timeout() -> None:
    kind = JobKind(name="test.defaults", payload_codec=JSON, result_codec=JSON)
    broker = build_broker(Settings.for_tests())
    try:
        register_tasks(broker, [job_task(kind, _pick_body)])
    finally:
        await broker.shutdown()


def test_a_job_task_takes_a_shorter_backstop() -> None:
    backstop = timedelta(seconds=3)

    definition = job_task(KIND, _pick_body, backstop=backstop)

    assert definition.timeout == KIND.time_limit + backstop


def test_a_message_kicked_by_name_gets_the_registered_timeout(
    middleware: RegisteredLabelsMiddleware,
) -> None:
    message = middleware.pre_execute(_message("task", {}))

    assert message.labels["timeout"] == 3.0


def test_the_messages_own_labels_win(middleware: RegisteredLabelsMiddleware) -> None:
    message = middleware.pre_execute(_message("task", {"timeout": 1.0}))

    assert message.labels["timeout"] == 1.0


def test_an_unknown_task_keeps_its_labels(
    middleware: RegisteredLabelsMiddleware,
) -> None:
    message = middleware.pre_execute(_message("unknown", {"a": "b"}))

    assert message.labels == {"a": "b"}


def test_get_use_cases_fails_before_the_worker_starts() -> None:
    with pytest.raises(RuntimeError, match="startup hasn't run"):
        get_use_cases(TaskiqState())
