from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from redis.asyncio import Redis

from tally_aloft.adapters.outbound.postgres.health import PostgresCheck
from tally_aloft.adapters.outbound.postgres.unit_of_work import unit_of_work_factory
from tally_aloft.adapters.outbound.redis.health import ValkeyCheck
from tally_aloft.adapters.outbound.taskiq.job_queue import TaskiqJobQueue
from tally_aloft.application.registry import UseCases
from tally_aloft.application.shared.check_readiness import CheckReadiness
from tally_aloft.application.shared.jobs.claim_job import ClaimJob
from tally_aloft.application.shared.jobs.complete_job import CompleteJob
from tally_aloft.application.shared.jobs.fail_job_attempt import FailJobAttempt
from tally_aloft.application.shared.jobs.renew_job_lease import RenewJobLease
from tally_aloft.application.shared.jobs.sweep_jobs import SweepJobs
from tally_aloft.bootstrap.broker import idle_timeout

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from http import HTTPStatus

    from taskiq_redis import RedisStreamBroker

    from tally_aloft.adapters.inbound.jobs.tasks import TaskDefinition
    from tally_aloft.application.ports.health import DependencyCheck
    from tally_aloft.bootstrap.resources import Resources
    from tally_aloft.bootstrap.settings import Settings
    from tally_aloft.domain.shared.errors import CodedError

# Every task the production worker runs. Job kinds and scheduled tasks join
# it as features and maintenance jobs are built.
TASKS: tuple[TaskDefinition, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Overrides:
    """
    Test replacements for adapters and HTTP configuration, passed to `create_app`.

    Each story that adds an adapter adds an optional field for it here.
    """

    # Statuses for test-only errors, merged over the production error table.
    error_statuses: Mapping[type[CodedError], HTTPStatus] = field(default_factory=dict)
    # Readiness checks in place of the real ones. `None` builds the real ones;
    # `()` means no checks at all, whatever later stories add.
    dependency_checks: tuple[DependencyCheck, ...] | None = None


def build_use_cases(
    settings: Settings,
    resources: Resources,
    overrides: Overrides,
    kind_names: frozenset[str],
) -> UseCases:
    """
    Build every adapter, on the opened `resources`, and the `UseCases` registry.

    `kind_names` are the job kinds this process's workers register, which the
    sweeper keeps; it fails jobs of any other kind. The API builds the same
    registry but never sweeps, so its kind names go unused.
    """
    checks = (
        _dependency_checks(resources)
        if overrides.dependency_checks is None
        else overrides.dependency_checks
    )
    uow = unit_of_work_factory(resources.engine)
    return UseCases(
        check_readiness=CheckReadiness(checks),
        claim_job=ClaimJob(uow),
        renew_job_lease=RenewJobLease(uow),
        complete_job=CompleteJob(uow),
        fail_job_attempt=FailJobAttempt(uow),
        sweep_jobs=SweepJobs(
            uow=uow,
            queue=TaskiqJobQueue(resources.broker),
            kind_names=kind_names,
            stale_after=timedelta(seconds=settings.job_stale_after),
            batch_size=settings.sweep_batch_size,
        ),
    )


def job_kind_names(tasks: Sequence[TaskDefinition]) -> frozenset[str]:
    """Return the name of every job kind that a task in `tasks` runs."""
    return frozenset(task.kind_name for task in tasks if task.kind_name is not None)


def _dependency_checks(resources: Resources) -> tuple[DependencyCheck, ...]:
    """Return the real readiness checks, one per data store."""
    return (
        PostgresCheck(resources.engine),
        ValkeyCheck(resources.cache_client, name="cache"),
        # Through the broker's own connection pool, which the cache doesn't use.
        ValkeyCheck(Redis(connection_pool=resources.broker.connection_pool), "broker"),
    )


def register_tasks(broker: RedisStreamBroker, tasks: Sequence[TaskDefinition]) -> None:
    """
    Register `tasks` on `broker`, each under its name with its timeout.

    It raises `ValueError`, registering nothing, when two tasks share a name,
    which Taskiq would silently resolve to the last, or when the broker's idle
    timeout isn't greater than the longest task timeout: another worker could
    then reclaim a message whose task is still running.
    """
    duplicates = [
        name for name, count in Counter(t.name for t in tasks).items() if count > 1
    ]
    if len(duplicates) > 0:
        msg = f"task names must be unique: {', '.join(sorted(duplicates))}"
        raise ValueError(msg)
    idle = idle_timeout(broker)
    longest = max((task.timeout for task in tasks), default=timedelta(0))
    if idle <= longest:
        msg = (
            f"TALLY_BROKER_IDLE_TIMEOUT ({_seconds(idle)}) must be greater "
            f"than the longest task timeout ({_seconds(longest)})"
        )
        raise ValueError(msg)
    for task in tasks:
        _ = broker.register_task(
            task.function, task_name=task.name, timeout=task.timeout.total_seconds()
        )


def _seconds(duration: timedelta) -> str:
    """Return `duration` in seconds, as `630s` or `2.5s`."""
    return f"{duration.total_seconds():g}s"
