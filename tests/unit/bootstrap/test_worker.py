from datetime import timedelta

import pytest
from taskiq.schedule_sources import LabelScheduleSource

from tally_aloft.adapters.inbound.jobs.sweeper import SWEEP_INTERVAL
from tally_aloft.bootstrap import worker
from tally_aloft.bootstrap.container import TASKS
from tally_aloft.bootstrap.settings import Settings
from tally_aloft.bootstrap.worker import create_scheduler, create_worker


def _past_the_backstop(statement_timeout: int, most: int, backstop: int) -> str:
    return (
        rf"TALLY_DATABASE_STATEMENT_TIMEOUT \({statement_timeout}s\) must be at "
        rf"most {most}s, which JOB_BACKSTOP \({backstop}s\) covers"
    )


async def test_the_default_statement_timeout_fits_the_job_backstop() -> None:
    broker = create_worker(Settings.for_tests(), [])

    await broker.shutdown()


def test_a_statement_timeout_past_the_job_backstop_stops_the_worker() -> None:
    settings = Settings.for_tests(database_statement_timeout=31)

    with pytest.raises(ValueError, match=_past_the_backstop(31, 30, 140)):
        _ = create_worker(settings, [])


async def test_a_raised_job_backstop_takes_a_higher_statement_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker, "JOB_BACKSTOP", timedelta(seconds=142))
    settings = Settings.for_tests(database_statement_timeout=31)

    broker = create_worker(settings, [])

    await broker.shutdown()


def test_a_raised_job_backstop_still_stops_a_statement_timeout_past_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker, "JOB_BACKSTOP", timedelta(seconds=142))
    settings = Settings.for_tests(database_statement_timeout=32)

    with pytest.raises(ValueError, match=_past_the_backstop(32, 31, 142)):
        _ = create_worker(settings, [])


async def test_the_scheduler_reads_the_registered_schedules_without_connecting() -> (
    None
):
    # The database and Valkey URLs refuse connections, so a connection fails.
    broker = create_worker(Settings.for_tests(), TASKS)
    scheduler = create_scheduler(broker)
    [source] = scheduler.sources
    assert isinstance(source, LabelScheduleSource)
    assert source.broker is broker

    await source.startup()
    [schedule] = await source.get_schedules()
    await broker.shutdown()

    assert schedule.task_name == "jobs.sweep"
    assert schedule.schedule_id == "jobs.sweep"
    assert schedule.interval == 60
    assert schedule.labels == {"timeout": 50.0}


def test_a_never_dispatched_job_is_recovered_within_six_minutes() -> None:
    # The job is stale after the threshold, and the next sweep is at most
    # one interval later (NFR-12, AC 3).
    threshold = timedelta(seconds=Settings.for_tests().job_stale_after)

    assert threshold + SWEEP_INTERVAL <= timedelta(minutes=6)
