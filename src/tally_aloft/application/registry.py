from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tally_aloft.application.shared.check_readiness import CheckReadiness
    from tally_aloft.application.shared.delete_expired_idempotency_keys import (
        DeleteExpiredIdempotencyKeys,
    )
    from tally_aloft.application.shared.jobs.claim_job import ClaimJob
    from tally_aloft.application.shared.jobs.complete_job import CompleteJob
    from tally_aloft.application.shared.jobs.fail_job_attempt import FailJobAttempt
    from tally_aloft.application.shared.jobs.renew_job_lease import RenewJobLease
    from tally_aloft.application.shared.jobs.sweep_jobs import SweepJobs


@dataclass(frozen=True, slots=True, kw_only=True)
class UseCases:
    """
    Every use case that inbound adapters may call.

    Bootstrap builds it, and inbound adapters read it. Use cases never import it.
    """

    check_readiness: CheckReadiness
    # The job lifecycle, which the task wrapper orchestrates around a body.
    claim_job: ClaimJob
    renew_job_lease: RenewJobLease
    complete_job: CompleteJob
    fail_job_attempt: FailJobAttempt
    # Maintenance: recovers stalled jobs and fails those that can't run again.
    sweep_jobs: SweepJobs
    # Maintenance: deletes idempotency keys past their 24 hours.
    delete_expired_idempotency_keys: DeleteExpiredIdempotencyKeys
