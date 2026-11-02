from http import HTTPStatus

import pytest

from tally_aloft.adapters.inbound.http.errors import ERROR_STATUSES
from tally_aloft.application.shared.errors import IdempotencyKeyReusedError
from tally_aloft.domain.shared.ids import PilotId
from tests.fakes.clock import FakeClock
from tests.fakes.database import FakeDatabase
from tests.fakes.unit_of_work import fake_unit_of_work_factory
from tests.support.http import request
from tests.support.idempotency import IdempotentWrite
from tests.support.idempotency_routes import write_app

BODY = {"key": "key-1", "request_hash": "hash-1", "note": "one hour"}


@pytest.fixture
def database() -> FakeDatabase:
    return FakeDatabase(FakeClock())


async def test_a_write_answers_201_and_a_repeat_replays_it(
    database: FakeDatabase,
) -> None:
    app = write_app(IdempotentWrite(fake_unit_of_work_factory(database)), PilotId.new())

    first = await request(app, "POST", "/test/writes", json=BODY)
    repeat = await request(app, "POST", "/test/writes", json=BODY)

    assert first.status_code == HTTPStatus.CREATED
    assert first.json()["note"] == "one hour"
    assert repeat.status_code == HTTPStatus.CREATED
    assert repeat.json() == first.json()
    assert len(database.jobs) == 1


async def test_a_reused_key_answers_422_with_the_shared_body(
    database: FakeDatabase,
) -> None:
    app = write_app(IdempotentWrite(fake_unit_of_work_factory(database)), PilotId.new())
    _ = await request(app, "POST", "/test/writes", json=BODY)

    response = await request(
        app, "POST", "/test/writes", json={**BODY, "request_hash": "hash-2"}
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
    assert response.json() == {
        "code": "idempotency_key_reused",
        "message": "This Idempotency-Key was already used for a different request.",
        "details": {},
    }


def test_the_reuse_error_maps_to_422() -> None:
    assert ERROR_STATUSES[IdempotencyKeyReusedError] == HTTPStatus.UNPROCESSABLE_CONTENT
