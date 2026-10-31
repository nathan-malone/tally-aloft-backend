from typing import TYPE_CHECKING

from tally_aloft.application.shared.jobs.kinds import JobKind

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from types import ModuleType

    from tally_aloft.adapters.inbound.jobs.tasks import TaskDefinition


def kind_names(modules: Iterable[ModuleType]) -> set[str]:
    """Return the name of every `JobKind` defined at the top of `modules`."""
    return {
        value.name
        for module in modules
        for value in vars(module).values()
        if isinstance(value, JobKind)
    }


def kinds_without_a_task(
    names: Iterable[str], tasks: Sequence[TaskDefinition]
) -> list[str]:
    """Return, sorted, each kind name that no job task in `tasks` runs."""
    covered = {task.kind_name for task in tasks if task.kind_name is not None}
    return sorted(set(names) - covered)
