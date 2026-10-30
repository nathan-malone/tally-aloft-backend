from typing import TYPE_CHECKING, override

from taskiq import TaskiqMiddleware

if TYPE_CHECKING:
    from taskiq import TaskiqMessage


class RegisteredLabelsMiddleware(TaskiqMiddleware):
    """
    Give every message its task's registered labels, under its own.

    Taskiq sends registered labels, such as `timeout`, only when a task is
    kicked through its decorated function. A task kicked by name, as the job
    queue does, would otherwise run with no timeout at all.
    """

    @override
    def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        """Merge the registered task's labels under the message's own."""
        task = self.broker.find_task(message.task_name)
        if task is not None:
            message.labels = {**task.labels, **message.labels}
        return message
