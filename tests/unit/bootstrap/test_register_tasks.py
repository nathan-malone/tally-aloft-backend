from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from tally_aloft.adapters.inbound.jobs.tasks import TaskDefinition
from tally_aloft.bootstrap.broker import build_broker
from tally_aloft.bootstrap.container import TASKS, register_tasks
from tally_aloft.bootstrap.settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from taskiq_redis import RedisStreamBroker


async def _noop() -> None:
    return None


def _task(name: str, seconds: float) -> TaskDefinition:
    return TaskDefinition(name=name, function=_noop, timeout=timedelta(seconds=seconds))


@pytest.fixture
async def broker() -> AsyncGenerator[RedisStreamBroker]:
    """A broker whose idle_timeout is 10 seconds, which never connects."""
    broker = build_broker(Settings.for_tests(broker_idle_timeout=10))
    yield broker
    await broker.shutdown()


def test_an_empty_list_registers_nothing(broker: RedisStreamBroker) -> None:
    register_tasks(broker, [])

    assert broker.get_all_tasks() == {}


def test_the_production_list_registers(broker: RedisStreamBroker) -> None:
    register_tasks(broker, TASKS)

    assert set(broker.get_all_tasks()) == {task.name for task in TASKS}


def test_each_task_is_registered_under_its_name_with_its_timeout(
    broker: RedisStreamBroker,
) -> None:
    register_tasks(broker, [_task("a", 2), _task("b", 9.5)])

    tasks = broker.get_all_tasks()
    assert set(tasks) == {"a", "b"}
    assert tasks["a"].labels["timeout"] == 2.0
    assert tasks["b"].labels["timeout"] == 9.5


@pytest.mark.parametrize("seconds", [10, 11])
def test_a_timeout_not_under_idle_timeout_names_both_values(
    broker: RedisStreamBroker, seconds: int
) -> None:
    message = (
        rf"TALLY_BROKER_IDLE_TIMEOUT \(10s\) must be greater than the longest "
        rf"task timeout \({seconds}s\)"
    )

    with pytest.raises(ValueError, match=message):
        register_tasks(broker, [_task("short", 1), _task("long", seconds)])

    assert broker.get_all_tasks() == {}


def test_a_duplicate_name_is_rejected(broker: RedisStreamBroker) -> None:
    with pytest.raises(ValueError, match="task names must be unique: same"):
        register_tasks(broker, [_task("same", 1), _task("other", 1), _task("same", 2)])

    assert broker.get_all_tasks() == {}
