from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import FastAPI


async def request(  # noqa: PLR0913  # It mirrors the httpx options tests use.
    app: FastAPI,
    method: str,
    path: str,
    *,
    params: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
    json: object = None,
) -> httpx.Response:
    """Send one request to `app` in process, without running its lifespan."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        return await client.request(
            method, path, params=params, headers=headers, json=json
        )
