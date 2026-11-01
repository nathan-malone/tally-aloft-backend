from datetime import timedelta
from typing import TYPE_CHECKING

from tally_aloft.application.registry import UseCases
from tally_aloft.application.shared.check_readiness import CheckReadiness
from tally_aloft.application.shared.jobs.claim_job import ClaimJob
from tally_aloft.application.shared.jobs.complete_job import CompleteJob
from tally_aloft.application.shared.jobs.fail_job_attempt import FailJobAttempt
from tally_aloft.application.shared.jobs.renew_job_lease import RenewJobLease
from tally_aloft.application.shared.jobs.sweep_jobs import SweepJobs
from tally_aloft.bootstrap.container import job_kind_names
from tally_aloft.bootstrap.settings import Settings
from tests.fakes.job_queue import FakeJobQueue
from tests.support.jobs import TEST_JOB_TASKS

if TYPE_CHECKING:
    from collections.abc import Callable

    from tally_aloft.application.shared.jobs.ports import JobRunnerUnitOfWork


def job_use_cases(
    uow: Callable[[], JobRunnerUnitOfWork],
    *,
    renewal_uow: Callable[[], JobRunnerUnitOfWork] | None = None,
) -> UseCases:
    """
    Return `UseCases` whose job lifecycle runs on `uow`, and no readiness checks.

    `renewal_uow`, when given, serves lease renewal alone, so a test can see
    each renewal's unit of work. The sweeper, which these tests never run,
    keeps the test job kinds and dispatches to a `FakeJobQueue`, at the
    default settings; sweep tests build their own `SweepJobs`.
    """
    defaults = Settings.for_tests()
    return UseCases(
        check_readiness=CheckReadiness(()),
        claim_job=ClaimJob(uow),
        renew_job_lease=RenewJobLease(uow if renewal_uow is None else renewal_uow),
        complete_job=CompleteJob(uow),
        fail_job_attempt=FailJobAttempt(uow),
        sweep_jobs=SweepJobs(
            uow=uow,
            queue=FakeJobQueue(),
            kind_names=job_kind_names(TEST_JOB_TASKS),
            stale_after=timedelta(seconds=defaults.job_stale_after),
            batch_size=defaults.sweep_batch_size,
        ),
    )
