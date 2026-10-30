import sys

from tally_aloft.bootstrap.broker import build_broker
from tally_aloft.bootstrap.container import TASKS, register_tasks
from tally_aloft.bootstrap.settings import Settings
from tests.support import jobs
from tests.support.job_kinds import kind_names, kinds_without_a_task
from tests.support.modules import import_every_module
from tests.support.tasks import TEST_TASKS


async def test_every_production_job_kind_has_a_registered_task() -> None:
    names = kind_names(sys.modules[name] for name in import_every_module())
    broker = build_broker(Settings.for_tests())
    try:
        register_tasks(broker, TASKS)
        registered = set(broker.get_all_tasks())
    finally:
        await broker.shutdown()

    assert kinds_without_a_task(names, TASKS) == []
    assert names <= registered


def test_every_test_job_kind_has_a_task_in_the_test_worker() -> None:
    assert kinds_without_a_task(kind_names([jobs]), TEST_TASKS) == []


def test_a_task_list_that_leaves_a_kind_out_names_it() -> None:
    partial = [task for task in TEST_TASKS if task.kind_name != jobs.FAIL.name]

    assert kinds_without_a_task(kind_names([jobs]), partial) == ["test.fail"]
