from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

from taskiq import TaskiqEvents

from tally_aloft.adapters.inbound.jobs.labels import RegisteredLabelsMiddleware
from tally_aloft.bootstrap.broker import build_broker
from tally_aloft.bootstrap.container import Overrides, build_use_cases, register_tasks
from tally_aloft.bootstrap.resources import open_resources

if TYPE_CHECKING:
    from collections.abc import Sequence

    from taskiq import TaskiqState
    from taskiq_redis import RedisStreamBroker

    from tally_aloft.adapters.inbound.jobs.tasks import TaskDefinition
    from tally_aloft.bootstrap.settings import Settings


def create_worker(
    settings: Settings, tasks: Sequence[TaskDefinition]
) -> RedisStreamBroker:
    """
    Return a worker's broker, with `tasks` registered and its lifecycle wired.

    It runs when the worker module is imported, not at startup, because the
    scheduler imports the same module and runs no startup handlers. Startup
    opens the shared resources on this broker and puts `UseCases` into
    Taskiq's state; shutdown closes them.
    """
    broker = build_broker(settings)
    broker.add_middlewares(RegisteredLabelsMiddleware())
    stack = AsyncExitStack()

    async def start(state: TaskiqState) -> None:
        resources = await stack.enter_async_context(
            open_resources(settings, broker=broker)
        )
        state.use_cases = build_use_cases(settings, resources, Overrides())

    async def stop(_state: TaskiqState) -> None:
        await stack.aclose()

    broker.add_event_handler(TaskiqEvents.WORKER_STARTUP, start)
    broker.add_event_handler(TaskiqEvents.WORKER_SHUTDOWN, stop)
    register_tasks(broker, tasks)
    return broker
