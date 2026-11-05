"""
Test-only routes that put marker values where telemetry could pick them up.

Each route takes its marker from the request and makes it reach a request
body, a validation error, a database error or a cache value. The marker tests
then check that no exported span holds it. The image never mounts this module.
"""

from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import insert, text

from tally_aloft.adapters.outbound.postgres.unit_of_work import unit_of_work_factory
from tally_aloft.adapters.outbound.redis.cache import ValkeyCache
from tally_aloft.adapters.outbound.taskiq.job_queue import TaskiqJobQueue
from tally_aloft.bootstrap.app import create_app
from tally_aloft.bootstrap.container import Overrides
from tally_aloft.bootstrap.observability import instrument_app
from tally_aloft.bootstrap.settings import Settings
from tally_aloft.domain.shared.ids import PilotId
from tests.support.jobs import RecordTestJob
from tests.support.tables import telemetry_rows

if TYPE_CHECKING:
    from fastapi import FastAPI
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine
    from taskiq import AsyncBroker

PREFIX = "/test/telemetry"
CACHE_KEY = "telemetry"
# The two statements `/queries` runs, which tests find by their text.
FIRST_QUERY = "SELECT 1 AS first"
SECOND_QUERY = "SELECT 2 AS second"


class Marked(BaseModel):
    """A request body holding one marker."""

    marker: str


def telemetry_router(engine: AsyncEngine, cache_client: Redis) -> APIRouter:
    """Return the marker routes, on `engine` and the Valkey `cache_client`."""
    router = APIRouter(prefix=PREFIX, tags=["telemetry"])
    cache = ValkeyCache(cache_client)

    @router.post("/echo", status_code=HTTPStatus.NO_CONTENT)
    async def echo(body: Marked) -> None:
        _ = body

    @router.get("/validated", status_code=HTTPStatus.NO_CONTENT)
    async def validated(count: int) -> None:
        _ = count

    @router.get("/search", status_code=HTTPStatus.NO_CONTENT)
    async def search(q: str) -> None:
        _ = q

    @router.post("/queries", status_code=HTTPStatus.NO_CONTENT)
    async def queries(body: Marked) -> None:
        async with engine.connect() as connection:
            _ = await connection.execute(text(FIRST_QUERY))
            _ = await connection.execute(text(SECOND_QUERY))
        await cache.set(CACHE_KEY, body.marker.encode(), timedelta(minutes=1))

    @router.post("/unique", status_code=HTTPStatus.NO_CONTENT)
    async def unique(body: Marked) -> None:
        row = {"key": body.marker, "value": "first"}
        async with engine.begin() as connection:
            _ = await connection.execute(insert(telemetry_rows), [row, row])

    @router.post("/uuid", status_code=HTTPStatus.NO_CONTENT)
    async def uuid(body: Marked) -> None:
        # Cast on the server, so Postgres itself echoes the value.
        statement = text("SELECT CAST(CAST(:value AS text) AS uuid)")
        async with engine.connect() as connection:
            _ = await connection.execute(statement, {"value": body.marker})

    @router.post("/not-null", status_code=HTTPStatus.NO_CONTENT)
    async def not_null(body: Marked) -> None:
        async with engine.begin() as connection:
            _ = await connection.execute(
                insert(telemetry_rows), {"key": body.marker, "value": None}
            )

    @router.post("/cache", status_code=HTTPStatus.NO_CONTENT)
    async def cache_value(body: Marked) -> None:
        await cache.set(CACHE_KEY, body.marker.encode(), timedelta(minutes=1))

    return router


def jobs_router(engine: AsyncEngine, broker: AsyncBroker) -> APIRouter:
    """Return the route that records an `ECHO` job and dispatches it on `broker`."""
    router = APIRouter(prefix=PREFIX, tags=["telemetry"])
    record = RecordTestJob(unit_of_work_factory(engine), TaskiqJobQueue(broker))

    @router.post("/jobs", status_code=HTTPStatus.ACCEPTED)
    async def jobs(body: Marked) -> None:
        _ = await record(PilotId.new(), body.marker)

    return router


def telemetry_app(*routers: APIRouter) -> FastAPI:
    """Return the API with `routers`, traced as `main.py` traces it."""
    app = create_app(Settings.for_tests(), Overrides(dependency_checks=()))
    for router in routers:
        app.include_router(router)
    return instrument_app(app)
