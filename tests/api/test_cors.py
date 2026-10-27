from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

from tests.support.contract_routes import ORIGIN, contract_app
from tests.support.http import request


async def _preflight(origin: str, headers: str) -> httpx.Response:
    return await request(
        contract_app(),
        "OPTIONS",
        "/test/body",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": headers,
        },
    )


async def test_preflight_from_the_web_app_is_allowed_without_credentials() -> None:
    response = await _preflight(ORIGIN, "Idempotency-Key")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    allowed = response.headers["access-control-allow-headers"].lower().split(", ")
    assert "idempotency-key" in allowed
    assert "access-control-allow-credentials" not in response.headers


async def test_preflight_from_another_origin_is_not_allowed() -> None:
    response = await _preflight("https://evil.example", "Content-Type")

    assert "access-control-allow-origin" not in response.headers


async def test_preflight_for_an_unlisted_header_is_not_allowed() -> None:
    response = await _preflight(ORIGIN, "X-Other")

    assert response.status_code == 400
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "x-other" not in allowed


async def test_not_found_carries_cors_headers() -> None:
    response = await request(
        contract_app(), "GET", "/test/nowhere", headers={"Origin": ORIGIN}
    )

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    assert response.headers["access-control-allow-origin"] == ORIGIN
