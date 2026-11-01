from datetime import timedelta

from tally_aloft.adapters.inbound.jobs.tasks import TaskDefinition
from tally_aloft.adapters.outbound.taskiq.job_queue import TaskiqJobQueue
from tally_aloft.bootstrap.container import Overrides, build_use_cases, job_kind_names
from tally_aloft.bootstrap.resources import open_resources
from tally_aloft.bootstrap.settings import Settings
from tests.support.jobs import TEST_JOB_TASKS
from tests.support.tasks import PROBE_TASKS


async def _noop() -> None:
    pass


async def test_the_sweeper_takes_the_settings_and_the_kind_names() -> None:
    settings = Settings.for_tests(job_stale_after=60, sweep_batch_size=7)
    kinds = frozenset({"test.echo"})

    # Opening the resources connects nothing, so no data store is needed.
    async with open_resources(settings) as resources:
        use_cases = build_use_cases(settings, resources, Overrides(), kinds)

    sweep = use_cases.sweep_jobs
    assert sweep.kind_names == kinds
    assert sweep.stale_after == timedelta(seconds=60)
    assert sweep.batch_size == 7
    assert isinstance(sweep.queue, TaskiqJobQueue)
    assert sweep.queue.broker is resources.broker


def test_job_kind_names_are_the_kinds_job_tasks_run() -> None:
    other = TaskDefinition(name="other", function=_noop, timeout=timedelta(1))

    names = job_kind_names([*TEST_JOB_TASKS, *PROBE_TASKS, other])

    assert names == {task.name for task in TEST_JOB_TASKS}
