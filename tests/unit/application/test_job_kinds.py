from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid7

import pytest

from tally_aloft.application.shared.jobs.kinds import ClaimedJob, JobId, JobKind
from tally_aloft.domain.shared.ids import PilotId
from tests.support.codecs import JSON

if TYPE_CHECKING:
    from collections.abc import Callable


def test_a_kind_has_the_documented_defaults() -> None:
    kind = JobKind(name="k", payload_codec=JSON, result_codec=JSON)

    assert kind.max_attempts == 5
    assert kind.lease == timedelta(minutes=5)
    assert kind.time_limit == timedelta(minutes=10)


@pytest.mark.parametrize(
    "make",
    [
        lambda: JobKind(
            name="k", payload_codec=JSON, result_codec=JSON, max_attempts=0
        ),
        lambda: JobKind(
            name="k", payload_codec=JSON, result_codec=JSON, lease=timedelta(0)
        ),
        lambda: JobKind(
            name="k", payload_codec=JSON, result_codec=JSON, lease=timedelta(seconds=-1)
        ),
        lambda: JobKind(
            name="k", payload_codec=JSON, result_codec=JSON, time_limit=timedelta(0)
        ),
    ],
    ids=["no-attempts", "zero-lease", "negative-lease", "zero-time-limit"],
)
def test_a_kind_rejects_limits_no_job_could_run_under(
    make: Callable[[], object],
) -> None:
    with pytest.raises(ValueError, match="job kind k needs"):
        _ = make()


def test_with_payload_keeps_the_claim() -> None:
    claimed = ClaimedJob(
        id=JobId(uuid7()), pilot_id=PilotId.new(), kind="k", attempts=2, payload="raw"
    )

    decoded = claimed.with_payload(42)

    assert (decoded.id, decoded.pilot_id, decoded.kind, decoded.attempts) == (
        claimed.id,
        claimed.pilot_id,
        "k",
        2,
    )
    assert decoded.payload == 42
