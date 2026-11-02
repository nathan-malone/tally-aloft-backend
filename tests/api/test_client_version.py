from typing import TYPE_CHECKING

from tests.support.contract_routes import ORIGIN, contract_app
from tests.support.http import request

if TYPE_CHECKING:
    from fastapi import FastAPI

OUTDATED = {"X-Client-Platform": "ios", "X-Client-Version": "1.3.9"}


def _app() -> FastAPI:
    return contract_app(min_client_versions='{"ios": "1.4.0"}')


async def test_an_outdated_app_gets_the_shared_error_body() -> None:
    response = await request(
        _app(), "GET", "/test/query", params={"count": "1"}, headers=OUTDATED
    )

    assert response.status_code == 400
    assert response.json() == {
        "code": "client_version_unsupported",
        "message": (
            "This app version is no longer supported. Update the app to continue."
        ),
        "details": {"platform": "ios", "minimum_version": "1.4.0"},
    }


async def test_the_rejection_carries_cors_headers() -> None:
    response = await request(
        _app(),
        "GET",
        "/test/query",
        params={"count": "1"},
        headers={**OUTDATED, "Origin": ORIGIN},
    )

    assert response.status_code == 400
    assert response.headers["access-control-allow-origin"] == ORIGIN


async def test_a_current_app_reaches_the_route() -> None:
    response = await request(
        _app(),
        "GET",
        "/test/query",
        params={"count": "3"},
        headers={"X-Client-Platform": "ios", "X-Client-Version": "1.4.0"},
    )

    assert response.status_code == 200
    assert response.json() == 3


async def test_livez_is_exempt() -> None:
    response = await request(_app(), "GET", "/livez", headers=OUTDATED)

    assert response.status_code == 200


async def test_webhooks_are_exempt_and_not_in_the_schema() -> None:
    app = _app()

    response = await request(app, "POST", "/webhooks/test", headers=OUTDATED)

    assert response.status_code == 204
    assert "/webhooks/test" not in app.openapi()["paths"]
