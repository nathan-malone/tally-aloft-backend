from datetime import timedelta

import pytest
from taskiq import TaskiqMessage, TaskiqState

from tally_aloft.adapters.inbound.jobs.dependencies import get_use_cases
from tally_aloft.adapters.inbound.jobs.labels import RegisteredLabelsMiddleware
from tally_aloft.adapters.inbound.jobs.tasks import TaskDefinition
from tally_aloft.bootstrap.broker import build_broker
from tally_aloft.bootstrap.container import register_tasks
from tally_aloft.bootstrap.settings import Settings


async def _noop() -> None:
    return None


def _message(task_name: str, labels: dict[str, object]) -> TaskiqMessage:
    return TaskiqMessage(
        task_id="id",
        task_name=task_name,
        labels=labels,
        labels_types={},
        args=[],
        kwargs={},
    )


async def test_a_message_kicked_by_name_gets_the_registered_timeout() -> None:
    broker = build_broker(Settings.for_tests())
    try:
        definition = TaskDefinition(
            name="task", function=_noop, timeout=timedelta(seconds=3)
        )
        register_tasks(broker, [definition])
        middleware = RegisteredLabelsMiddleware()
        middleware.set_broker(broker)

        message = middleware.pre_execute(_message("task", {}))
    finally:
        await broker.shutdown()

    assert message.labels["timeout"] == 3.0


async def test_the_messages_own_labels_win() -> None:
    broker = build_broker(Settings.for_tests())
    try:
        definition = TaskDefinition(
            name="task", function=_noop, timeout=timedelta(seconds=3)
        )
        register_tasks(broker, [definition])
        middleware = RegisteredLabelsMiddleware()
        middleware.set_broker(broker)

        message = middleware.pre_execute(_message("task", {"timeout": 1.0}))
    finally:
        await broker.shutdown()

    assert message.labels["timeout"] == 1.0


async def test_an_unknown_task_keeps_its_labels() -> None:
    broker = build_broker(Settings.for_tests())
    try:
        middleware = RegisteredLabelsMiddleware()
        middleware.set_broker(broker)

        message = middleware.pre_execute(_message("unknown", {"a": "b"}))
    finally:
        await broker.shutdown()

    assert message.labels == {"a": "b"}


def test_get_use_cases_fails_before_the_worker_starts() -> None:
    with pytest.raises(RuntimeError, match="startup hasn't run"):
        get_use_cases(TaskiqState())
