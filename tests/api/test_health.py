import time
from typing import TYPE_CHECKING

from tally_aloft.bootstrap.app import create_app
from tally_aloft.bootstrap.container import Overrides
from tally_aloft.bootstrap.settings import Settings
from tests.fakes.health import FakeDependencyCheck
from tests.support.http import request

if TYPE_CHECKING:
    import httpx

# NFR-6: /readyz answers within 3 seconds even when a check hangs.
READYZ_DEADLINE_SECONDS = 3


async def _readyz(*checks: FakeDependencyCheck) -> httpx.Response:
    app = create_app(Settings.for_tests(), Overrides(dependency_checks=checks))
    async with app.router.lifespan_context(app):
        return await request(app, "GET", "/readyz")


async def test_readyz_without_checks_is_ready_and_not_in_the_schema() -> None:
    response = await _readyz()

    assert response.status_code == 200
    app = create_app(Settings.for_tests())
    assert "/readyz" not in app.openapi()["paths"]


async def test_readyz_names_only_the_failed_check_without_its_error() -> None:
    response = await _readyz(
        FakeDependencyCheck("passing", "pass"),
        FakeDependencyCheck("failing", "fail", error_text="boom"),
    )

    assert response.status_code == 503
    assert response.json() == {
        "code": "not_ready",
        "message": "Some dependencies are not ready.",
        "details": {"failed_checks": ["failing"]},
    }
    assert "boom" not in response.text


async def test_readyz_answers_in_time_when_a_check_hangs() -> None:
    started = time.monotonic()

    response = await _readyz(FakeDependencyCheck("stuck", "hang"))

    assert time.monotonic() - started < READYZ_DEADLINE_SECONDS
    assert response.status_code == 503
    assert response.json()["details"] == {"failed_checks": ["stuck"]}
