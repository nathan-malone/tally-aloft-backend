from typing import TYPE_CHECKING

from tests.fakes.cache import FakeCache
from tests.fakes.clock import FakeClock
from tests.fakes.database import FakeDatabase
from tests.fakes.health import FakeDependencyCheck
from tests.fakes.job_queue import FakeJobQueue
from tests.fakes.jobs import FakeJobRepository, FakeJobRunner, FakeJobTable
from tests.fakes.unit_of_work import FakeUnitOfWork

if TYPE_CHECKING:
    from tally_aloft.application.ports.cache import Cache
    from tally_aloft.application.ports.clock import Clock
    from tally_aloft.application.ports.health import DependencyCheck
    from tally_aloft.application.ports.job_queue import JobQueue
    from tally_aloft.application.shared.jobs.ports import (
        JobRepository,
        JobRunner,
        JobRunnerUnitOfWork,
    )

# Each fake is assigned to a variable typed as its port, so the type check
# fails when a fake drifts from the Protocol it stands in for.
_clock: Clock = FakeClock()
_cache: Cache = FakeCache(now=_clock.now)
_dependency_check: DependencyCheck = FakeDependencyCheck(name="fake", outcome="pass")
# JobRunnerUnitOfWork extends SharedUnitOfWork, so this checks both.
_unit_of_work: JobRunnerUnitOfWork = FakeUnitOfWork(FakeDatabase(_clock))
_job_table = FakeJobTable(rows={}, changed=set(), clock=_clock)
_jobs: JobRepository = FakeJobRepository(_job_table)
_job_runner: JobRunner = FakeJobRunner(_job_table)
_job_queue: JobQueue = FakeJobQueue()
