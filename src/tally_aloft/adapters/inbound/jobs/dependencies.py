from typing import Annotated

from taskiq import TaskiqDepends, TaskiqState

from tally_aloft.application.registry import UseCases


def get_use_cases(state: TaskiqState = TaskiqDepends()) -> UseCases:  # noqa: B008  # TaskiqDepends is how Taskiq injects; it isn't called per call.
    """Return the `UseCases` the worker's startup put into Taskiq's state."""
    use_cases: object = state.get("use_cases")
    if not isinstance(use_cases, UseCases):
        msg = "the worker's state has no use_cases; its startup hasn't run"
        raise RuntimeError(msg)  # noqa: TRY004  # A wiring fault, not a bad argument.
    return use_cases


# How every task reaches the use cases. Taskiq reads task annotations at run
# time, so modules that use it import `UseCases` outside TYPE_CHECKING.
UseCasesDep = Annotated[UseCases, TaskiqDepends(get_use_cases)]
