import json
from typing import TYPE_CHECKING

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import Link, SpanContext, SpanKind, Status, StatusCode

from tally_aloft.bootstrap.scrubbing import REDACTED, SCRUBBED_MARK, Redactor
from tally_aloft.bootstrap.span_scrubbing import ScrubbingSpanExporter, scrub_span
from tests.support.database_errors import MARKER
from tests.support.telemetry import attributes_of, span_text

if TYPE_CHECKING:
    from opentelemetry.util.types import Attributes

CONTEXT = SpanContext(trace_id=1, span_id=2, is_remote=False)
# A secret setting's value.
HIDDEN = "hidden-marker-value"
NO_SECRETS = Redactor(frozenset())


def span(
    attributes: Attributes = None,
    events: tuple[Event, ...] = (),
    status: Status | None = None,
    name: str = "span",
) -> ReadableSpan:
    return ReadableSpan(
        name=name,
        context=CONTEXT,
        parent=SpanContext(trace_id=1, span_id=3, is_remote=True),
        resource=Resource.create({"service.name": "test"}),
        attributes=attributes,
        events=events,
        links=[Link(CONTEXT, {"link": "attribute"})],
        kind=SpanKind.SERVER,
        status=Status(StatusCode.UNSET) if status is None else status,
        start_time=10,
        end_time=20,
        instrumentation_scope=InstrumentationScope("scope", "1.0"),
    )


def exception_event(**attributes: str | bool) -> Event:
    return Event("exception", attributes, timestamp=15)


def test_a_marked_event_keeps_only_the_allowlist() -> None:
    event = exception_event(
        **{
            "exception.type": "ValueError",
            "exception.message": "ValueError",
            "exception.stacktrace": "Traceback ...",
            "exception.escaped": "False",
            "exception.logfire.data": json.dumps([{"input": MARKER}]),
            "recorded_by_logfire_fastapi": True,
            SCRUBBED_MARK: True,
        }
    )

    [scrubbed] = scrub_span(span(events=(event,)), NO_SECRETS).events

    assert set(attributes_of(scrubbed)) == {
        "exception.type",
        "exception.message",
        "exception.stacktrace",
        "exception.escaped",
        SCRUBBED_MARK,
    }


def test_an_unmarked_event_loses_its_text() -> None:
    event = exception_event(
        **{
            "exception.type": "sqlalchemy.exc.IntegrityError",
            "exception.message": MARKER,
            "exception.stacktrace": f"... {MARKER}",
        }
    )

    [scrubbed] = scrub_span(span(events=(event,)), NO_SECRETS).events

    assert attributes_of(scrubbed) == {
        "exception.type": "sqlalchemy.exc.IntegrityError",
        "exception.message": "sqlalchemy.exc.IntegrityError",
    }


def test_a_status_description_keeps_only_the_type() -> None:
    status = Status(StatusCode.ERROR, f"IntegrityError: {MARKER}")

    scrubbed = scrub_span(span(status=status), NO_SECRETS)

    assert scrubbed.status.status_code is StatusCode.ERROR
    assert scrubbed.status.description == "IntegrityError"


def test_validation_data_keeps_only_location_and_type() -> None:
    data = json.dumps([{"loc": ["count"], "type": "int_parsing", "input": MARKER}])

    scrubbed = scrub_span(span({"exception.logfire.data": data}), NO_SECRETS)

    assert json.loads(str(attributes_of(scrubbed)["exception.logfire.data"])) == [
        {"loc": ["count"], "type": "int_parsing"}
    ]


def test_drops_the_client_address_and_the_query_but_keeps_the_path() -> None:
    attributes = {
        "client.address": "203.0.113.7",
        "net.peer.ip": "203.0.113.7",
        "http.client_ip": "203.0.113.7",
        "url.query": f"q={MARKER}",
        "http.url": f"http://api/search?q={MARKER}",
        "http.target": "/search",
        "logfire.msg": f"GET /search ? q='{MARKER}'",
    }

    scrubbed = scrub_span(span(attributes), NO_SECRETS)

    assert attributes_of(scrubbed) == {
        "http.url": "http://api/search",
        "http.target": "/search",
        "logfire.msg": "GET /search",
    }


def test_an_ordinary_message_with_a_question_mark_is_kept() -> None:
    attributes = {"logfire.msg": "Is it ready ? yes"}

    scrubbed = scrub_span(span(attributes), NO_SECRETS)

    assert attributes_of(scrubbed) == attributes


def test_redacts_secrets_everywhere() -> None:
    status = Status(StatusCode.ERROR, f"Error {HIDDEN}")
    event = Event("log", {"text": f"a {HIDDEN} b", "list": (HIDDEN, "x")})

    scrubbed = scrub_span(
        span({"key": f"x{HIDDEN}y"}, (event,), status, name=HIDDEN),
        Redactor(frozenset({HIDDEN})),
    )

    assert HIDDEN not in span_text(scrubbed)
    assert REDACTED in scrubbed.name
    assert attributes_of(scrubbed)["key"] == f"x{REDACTED}y"


def test_an_untouched_span_round_trips() -> None:
    original = span({"http.route": "/x", "count": 3}, (Event("log", {"a": "b"}, 15),))

    scrubbed = scrub_span(original, NO_SECRETS)

    assert scrubbed.name == original.name
    assert scrubbed.context == original.context
    assert scrubbed.parent == original.parent
    assert scrubbed.resource == original.resource
    assert scrubbed.kind == original.kind
    assert scrubbed.start_time == original.start_time
    assert scrubbed.end_time == original.end_time
    assert scrubbed.instrumentation_scope == original.instrumentation_scope
    assert scrubbed.status.status_code == original.status.status_code
    assert attributes_of(scrubbed) == attributes_of(original)
    assert [(e.name, attributes_of(e), e.timestamp) for e in scrubbed.events] == [
        ("log", {"a": "b"}, 15)
    ]
    assert [(link.context, attributes_of(link)) for link in scrubbed.links] == [
        (CONTEXT, {"link": "attribute"})
    ]


def test_the_exporter_passes_scrubbed_spans_to_its_inner_exporter() -> None:
    inner = InMemorySpanExporter()
    exporter = ScrubbingSpanExporter(inner, frozenset({HIDDEN}))

    result = exporter.export([span({"key": HIDDEN})])

    assert result is SpanExportResult.SUCCESS
    [exported] = inner.get_finished_spans()
    assert HIDDEN not in span_text(exported)


def test_a_status_description_without_a_type_is_dropped() -> None:
    status = Status(StatusCode.ERROR, f"failed for {MARKER}")

    scrubbed = scrub_span(span(status=status), NO_SECRETS)

    assert scrubbed.status.status_code is StatusCode.ERROR
    assert scrubbed.status.description is None


def test_drops_captured_headers() -> None:
    attributes = {
        "http.request.header.x_forwarded_for": ("203.0.113.7",),
        "http.response.header.set_cookie": (MARKER,),
        "http.route": "/x",
    }

    scrubbed = scrub_span(span(attributes), NO_SECRETS)

    assert attributes_of(scrubbed) == {"http.route": "/x"}
