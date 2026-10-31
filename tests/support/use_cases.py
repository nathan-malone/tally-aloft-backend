from typing import TYPE_CHECKING

from tally_aloft.application.registry import UseCases
from tally_aloft.application.shared.check_readiness import CheckReadiness
from tally_aloft.application.shared.jobs.claim_job import ClaimJob
from tally_aloft.application.shared.jobs.complete_job import CompleteJob
from tally_aloft.application.shared.jobs.fail_job_attempt import FailJobAttempt
from tally_aloft.application.shared.jobs.renew_job_lease import RenewJobLease

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
    each renewal's unit of work.
    """
    return UseCases(
        check_readiness=CheckReadiness(()),
        claim_job=ClaimJob(uow),
        renew_job_lease=RenewJobLease(uow if renewal_uow is None else renewal_uow),
        complete_job=CompleteJob(uow),
        fail_job_attempt=FailJobAttempt(uow),
    )
