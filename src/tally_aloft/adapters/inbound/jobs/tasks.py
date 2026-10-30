from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import timedelta


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskDefinition:
    """
    One Taskiq task, as bootstrap registers it.

    `name` is the task's stable name, which messages carry. `timeout` is
    Taskiq's limit on the whole function. A job task names the kind it runs
    in `kind_name`; other tasks, such as scheduled ones, leave it `None`.
    """

    name: str
    function: Callable[..., Awaitable[object]]
    timeout: timedelta
    kind_name: str | None = None
