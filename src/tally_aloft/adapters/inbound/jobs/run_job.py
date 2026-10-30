"""
The task wrapper: claim a job, run its body under a lease, record the outcome.

The wrapper only orchestrates the lifecycle use cases; each opens its own unit
of work. While the body runs, a separate task renews the lease, so the body
never outlives the lease the database holds for it.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tally_aloft.application.shared.jobs.kinds import (
    LEASE_LOST,
    TIMEOUT,
    UNHANDLED_ERROR,
)
from tally_aloft.domain.shared.errors import CodedError

if TYPE_CHECKING:
    from tally_aloft.application.registry import UseCases
    from tally_aloft.application.shared.codec import JsonValue
    from tally_aloft.application.shared.jobs.kinds import (
        ClaimedJob,
        JobBody,
        JobId,
        JobKind,
    )

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Completed:
    """The body returned: its encoded result."""

    result: JsonValue


@dataclass(frozen=True, slots=True)
class _Failed:
    """The body failed: the attempt's error code."""

    error_code: str


async def run_job[P, R](
    use_cases: UseCases, kind: JobKind[P, R], body: JobBody[P, R], job_id: JobId
) -> None:
    """
    Run one delivery of the job `job_id`, exactly once while its lease holds.

    A delivery that can't claim the job runs nothing. Once claimed, the body
    runs under the kind's time limit while a side task renews the lease every
    half lease. If the lease is lost, the body is cancelled and nothing is
    written. Otherwise the job is completed, or its attempt failed with the
    body's error code, `timeout` or `unhandled_error`.
    """
    loop = asyncio.get_running_loop()
    claim_started = loop.time()
    claimed = await use_cases.claim_job(job_id, kind)
    if claimed is None:
        _logger.debug("Job %s (%s) isn't free to claim", job_id.value, kind.name)
        return
    work = asyncio.create_task(_run_body(kind, body, claimed))
    renewal = asyncio.create_task(
        _renew_until_lost(use_cases, kind, claimed, claim_started)
    )
    try:
        _ = await asyncio.wait({work, renewal}, return_when=asyncio.FIRST_COMPLETED)
        # Renewal ends only when the lease is lost, which outranks the body.
        if renewal.done():
            _log_lease_lost(claimed)
            return
        await _stop(renewal)
        await _record(use_cases, claimed, work.result())
    finally:
        await _stop(work)
        await _stop(renewal)


async def _run_body[P, R](
    kind: JobKind[P, R], body: JobBody[P, R], claimed: ClaimedJob[JsonValue]
) -> _Completed | _Failed:
    """
    Decode the payload, run the body under the time limit, encode the result.

    Decoding happens here, after the claim's commit, so a payload that won't
    decode fails the attempt like any body error, rather than leaving the
    claimed row `running` with no attempt recorded.
    """
    limit = asyncio.timeout(kind.time_limit.total_seconds())
    try:
        payload = kind.payload_codec.decode(claimed.payload)
        async with limit:
            result = await body(claimed.with_payload(payload))
        return _Completed(kind.result_codec.encode(result))
    except CodedError as error:
        return _Failed(error.code)
    except Exception as error:
        # The body's own TimeoutError, within the limit, is an unhandled one.
        if isinstance(error, TimeoutError) and limit.expired():
            return _Failed(TIMEOUT)
        _logger.exception(
            "Job %s (%s) attempt %d raised %s",
            claimed.id.value,
            claimed.kind,
            claimed.attempts,
            type(error).__qualname__,
        )
        return _Failed(UNHANDLED_ERROR)


async def _renew_until_lost[P, R](
    use_cases: UseCases,
    kind: JobKind[P, R],
    claimed: ClaimedJob[JsonValue],
    claim_started: float,
) -> None:
    """
    Renew the lease every half lease, and return once it is lost.

    The lease is lost when a renewal finds another claim has taken the job,
    or when no renewal has succeeded in time: each lease runs from the start
    of the call that set it, less a tenth for safety. A renewal that raises is
    retried after a tenth of the lease.
    """
    loop = asyncio.get_running_loop()
    lease = kind.lease.total_seconds()
    held_until = claim_started + lease * 0.9
    wait = lease / 2
    while True:
        await asyncio.sleep(max(0.0, min(wait, held_until - loop.time())))
        if loop.time() >= held_until:
            return
        started = loop.time()
        try:
            async with asyncio.timeout_at(held_until):
                renewed = await use_cases.renew_job_lease(
                    claimed.id, claimed.attempts, kind.lease
                )
        except Exception as error:  # noqa: BLE001  # Retried until the lease runs out.
            _logger.warning(
                "Renewing job %s (%s) failed: %s",
                claimed.id.value,
                claimed.kind,
                type(error).__qualname__,
            )
            wait = lease / 10
            continue
        if not renewed:
            return
        held_until = started + lease * 0.9
        wait = lease / 2


async def _record(
    use_cases: UseCases, claimed: ClaimedJob[JsonValue], outcome: _Completed | _Failed
) -> None:
    """Complete the job or fail its attempt, fenced on the claimed attempts."""
    if isinstance(outcome, _Completed):
        written = await use_cases.complete_job(
            claimed.id, claimed.attempts, outcome.result
        )
    else:
        written = await use_cases.fail_job_attempt(
            claimed.id, claimed.attempts, outcome.error_code
        )
    if not written:
        _log_lease_lost(claimed)


async def _stop(task: asyncio.Task[object]) -> None:
    """Cancel `task` if it is still running, and wait for it to end."""
    if not task.done():
        _ = task.cancel()
        _ = await asyncio.wait({task})


def _log_lease_lost(claimed: ClaimedJob[JsonValue]) -> None:
    _logger.warning(
        "%s: job %s (%s) attempt %d lost its lease and wrote nothing",
        LEASE_LOST,
        claimed.id.value,
        claimed.kind,
        claimed.attempts,
    )
