"""What the API exports to telemetry, on real Postgres and Valkey."""

import json
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from opentelemetry.trace import format_trace_id
from sqlalchemy.ext.asyncio import create_async_engine

from tally_aloft.adapters.inbound.http.errors import INTERNAL_ERROR
from tally_aloft.adapters.outbound.redis.client import build_client
from tally_aloft.bootstrap.observability import instrument_cache_client
from tally_aloft.bootstrap.scrubbing import SQLSTATE_ATTRIBUTE
from tests.support.http import request
from tests.support.telemetry import attributes_of, configured_telemetry
from tests.support.telemetry_routes import (
    FIRST_QUERY,
    PREFIX,
    SECOND_QUERY,
    telemetry_app,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import FastAPI

    from tests.support.postgres import PostgresDatabase
    from tests.support.telemetry import Telemetry

pytestmark = pytest.mark.container

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT_ID = "00f067aa0ba902b7"
# Logfire's level number for debug; a span without one is at info.
DEBUG_LEVEL = 5


@pytest.fixture
async def app(
    database: PostgresDatabase,
    valkey_url: str,
    telemetry: Telemetry,  # noqa: ARG001  # Logfire is configured before the app is traced.
) -> AsyncGenerator[FastAPI]:
    engine = create_async_engine(database.app_url)
    cache_client = build_client(valkey_url)
    instrument_cache_client(cache_client)
    yield telemetry_app(engine, cache_client)
    await cache_client.aclose()
    await engine.dispose()


def traceparent(flags: str) -> dict[str, str]:
    return {"traceparent": f"00-{TRACE_ID}-{PARENT_ID}-{flags}"}


async def test_a_request_continues_the_clients_trace_with_one_span_per_call(
    app: FastAPI, telemetry: Telemetry
) -> None:
    response = await request(
        app,
        "POST",
        f"{PREFIX}/queries",
        headers=traceparent("01"),
        json={"marker": "trace-marker"},
    )

    assert response.status_code == HTTPStatus.NO_CONTENT
    [request_span] = telemetry.request_spans()
    assert request_span.context is not None
    assert format_trace_id(request_span.context.trace_id) == TRACE_ID
    level: object = attributes_of(request_span).get("logfire.level_num")
    assert not isinstance(level, int) or level > DEBUG_LEVEL
    assert len(telemetry.query_spans(FIRST_QUERY)) == 1
    assert len(telemetry.query_spans(SECOND_QUERY)) == 1
    scopes = [span.instrumentation_scope for span in telemetry.spans()]
    assert not any(scope is not None and "sqlalchemy" in scope.name for scope in scopes)
    assert len(telemetry.cache_spans()) == 1


async def test_a_request_the_client_didnt_sample_is_still_recorded(
    app: FastAPI, telemetry: Telemetry
) -> None:
    _ = await request(
        app,
        "POST",
        f"{PREFIX}/echo",
        headers=traceparent("00"),
        json={"marker": "unsampled-marker"},
    )

    [request_span] = telemetry.request_spans()
    assert request_span.context is not None
    assert format_trace_id(request_span.context.trace_id) == TRACE_ID


async def test_a_request_body_is_never_exported(
    app: FastAPI, telemetry: Telemetry
) -> None:
    response = await request(
        app, "POST", f"{PREFIX}/echo", json={"marker": "body-marker-123"}
    )

    assert response.status_code == HTTPStatus.NO_CONTENT
    telemetry.assert_absent("body-marker-123")


async def test_a_validation_failure_is_recorded_without_its_input(
    app: FastAPI, telemetry: Telemetry
) -> None:
    response = await request(
        app, "GET", f"{PREFIX}/validated", params={"count": "validation-marker-456"}
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
    telemetry.assert_absent("validation-marker-456")
    [request_span] = telemetry.request_spans()
    errors = attributes_of(request_span)["fastapi.arguments.errors"]
    assert json.loads(str(errors)) == [
        {"loc": ["query", "count"], "type": "int_parsing"}
    ]


@pytest.mark.parametrize(
    ("route", "marker", "sqlstate"),
    [
        ("unique", "sql-marker-789", "23505"),
        ("uuid", "uuid-marker-101", "22P02"),
        ("not-null", "row-marker-202", "23502"),
    ],
)
async def test_a_database_error_is_exported_as_its_type_and_sqlstate(
    app: FastAPI, telemetry: Telemetry, route: str, marker: str, sqlstate: str
) -> None:
    response = await request(app, "POST", f"{PREFIX}/{route}", json={"marker": marker})

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.json()["code"] == INTERNAL_ERROR
    telemetry.assert_absent(marker)
    events = [
        event
        for event in telemetry.exception_events()
        if event.get(SQLSTATE_ATTRIBUTE) == sqlstate
    ]
    assert len(events) > 0
    assert all(str(event["exception.type"]) != "" for event in events)


async def test_a_cache_value_is_never_exported(
    app: FastAPI, telemetry: Telemetry
) -> None:
    response = await request(
        app, "POST", f"{PREFIX}/cache", json={"marker": "cache-marker-404"}
    )

    assert response.status_code == HTTPStatus.NO_CONTENT
    telemetry.assert_absent("cache-marker-404")
    [span] = telemetry.cache_spans()
    assert attributes_of(span).get("db.statement") == "SET ? ? ? ?"


async def test_the_client_address_and_query_are_dropped_but_the_path_kept(
    app: FastAPI, telemetry: Telemetry
) -> None:
    response = await request(
        app,
        "GET",
        f"{PREFIX}/search",
        params={"q": "query-marker-505"},
        client_address=("203.0.113.7", 50000),
    )

    assert response.status_code == HTTPStatus.NO_CONTENT
    telemetry.assert_absent("query-marker-505")
    telemetry.assert_absent("203.0.113.7")
    [request_span] = telemetry.request_spans()
    attributes = attributes_of(request_span)
    path = attributes.get("url.path", attributes.get("http.target"))
    assert path == f"{PREFIX}/search"


async def test_a_second_configuration_still_traces_queries(app: FastAPI) -> None:
    _ = configured_telemetry()
    telemetry = configured_telemetry()

    _ = await request(app, "POST", f"{PREFIX}/queries", json={"marker": "again"})

    assert len(telemetry.query_spans(FIRST_QUERY)) == 1
