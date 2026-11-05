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
    client_address: tuple[str, int] = ("127.0.0.1", 123),
) -> httpx.Response:
    """
    Send one request to `app` in process, without running its lifespan.

    `client_address` is where the request comes from; httpx's own by default.
    """
    transport = httpx.ASGITransport(app=app, client=client_address)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        return await client.request(
            method, path, params=params, headers=headers, json=json
        )
