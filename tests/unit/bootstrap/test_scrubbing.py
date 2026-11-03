import json
import logging
from typing import TYPE_CHECKING

import pytest
from logfire.types import ExceptionCallbackHelper
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import (
    Link,
    NonRecordingSpan,
    SpanContext,
    SpanKind,
    Status,
    StatusCode,
)
from sqlalchemy.exc import IntegrityError

from tally_aloft.bootstrap.scrubbing import (
    CONSTRAINT_ATTRIBUTE,
    REDACTED,
    SCRUBBED_MARK,
    SQLSTATE_ATTRIBUTE,
    TelemetryLogFilter,
    record_exception_safely,
    render_safely,
    safe_message,
    secret_values,
)
from tally_aloft.bootstrap.settings import Settings
from tally_aloft.bootstrap.span_scrubbing import ScrubbingSpanExporter, scrub_span
from tests.support.database_errors import MARKER, unique_violation
from tests.support.telemetry import attributes_of

if TYPE_CHECKING:
    from opentelemetry.util.types import Attributes

CONTEXT = SpanContext(trace_id=1, span_id=2, is_remote=False)
# A secret setting's value.
HIDDEN = "hidden-marker-value"


def raised[E: BaseException](error: E) -> E:
    """Return `error` with a traceback, as if it had been raised here."""

    def throw() -> None:
        raise error

    try:
        throw()
    except BaseException as caught:  # Any error is returned as raised.
        if caught is not error:
            raise
    return error


def database_error() -> IntegrityError:
    """Return SQLAlchemy's unique violation, raised from asyncpg's, with tracebacks."""
    cause = raised(unique_violation())
    wrapper = IntegrityError(f"INSERT ... DETAIL {MARKER}", None, cause)
    wrapper.__cause__ = cause
    return raised(wrapper)


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


def everything_in(scrubbed: ReadableSpan) -> str:
    """Return every string a span holds, as one text to search."""
    parts: list[object] = [
        scrubbed.name,
        attributes_of(scrubbed),
        scrubbed.status.description,
    ]
    parts += [(event.name, attributes_of(event)) for event in scrubbed.events]
    parts += [attributes_of(link) for link in scrubbed.links]
    return repr(parts)


def test_secret_values_include_url_passwords() -> None:
    settings = Settings.for_tests(
        database_url="postgresql+asyncpg://app:p%40ss-word@db:5432/tally",
        valkey_url="redis://:cache-pass@cache:6379/0",
        logfire_token="pylf_v1_us_token",  # noqa: S106  # A test value.
    )

    secrets = secret_values(settings)

    assert {"p%40ss-word", "p@ss-word", "cache-pass", "pylf_v1_us_token"} <= secrets
    assert "postgresql+asyncpg://app:p%40ss-word@db:5432/tally" in secrets
    assert "" not in secrets


def test_safe_message_names_a_database_error_without_its_text() -> None:
    message = safe_message(database_error())

    assert message == (
        "sqlalchemy.exc.IntegrityError (SQLSTATE 23505, constraint uq_key)"
    )


def test_safe_message_is_the_type_of_any_other_error() -> None:
    assert safe_message(ValueError(MARKER)) == "ValueError"


def test_render_safely_keeps_frames_and_no_message() -> None:
    rendered = render_safely(database_error())

    assert "Traceback (most recent call last):" in rendered
    assert "in raised" in rendered
    assert "The above exception was the direct cause" in rendered
    assert "asyncpg.exceptions.UniqueViolationError (SQLSTATE 23505" in rendered
    assert MARKER not in rendered
    assert "DETAIL" not in rendered


def test_render_safely_ignores_values_holding_newlines() -> None:
    error = raised(ValueError(f"line\n    {MARKER}\n  File x"))

    assert MARKER not in render_safely(error)


def test_render_safely_renders_group_members_without_text() -> None:
    group = raised(ExceptionGroup("failed", [raised(unique_violation())]))

    rendered = render_safely(group)

    assert "    | asyncpg.exceptions.UniqueViolationError" in rendered
    assert MARKER not in rendered


def test_the_callback_renders_a_database_error() -> None:
    helper = ExceptionCallbackHelper(
        span=NonRecordingSpan(CONTEXT),  # pyrefly: ignore[bad-argument-type]  # The callback never touches the span.
        exception=database_error(),
        event_attributes={},
    )

    record_exception_safely(helper)

    attributes = helper.event_attributes
    assert attributes[SCRUBBED_MARK] is True
    assert attributes[SQLSTATE_ATTRIBUTE] == "23505"
    assert attributes[CONSTRAINT_ATTRIBUTE] == "uq_key"
    assert MARKER not in repr(attributes)


def test_the_callback_records_only_the_type_of_another_error() -> None:
    helper = ExceptionCallbackHelper(
        span=NonRecordingSpan(CONTEXT),  # pyrefly: ignore[bad-argument-type]  # The callback never touches the span.
        exception=raised(ValueError(MARKER)),
        event_attributes={},
    )

    record_exception_safely(helper)

    assert helper.event_attributes["exception.message"] == "ValueError"
    assert SQLSTATE_ATTRIBUTE not in helper.event_attributes
    assert MARKER not in repr(helper.event_attributes)


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

    [scrubbed] = scrub_span(span(events=(event,)), frozenset()).events

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

    [scrubbed] = scrub_span(span(events=(event,)), frozenset()).events

    assert attributes_of(scrubbed) == {
        "exception.type": "sqlalchemy.exc.IntegrityError",
        "exception.message": "sqlalchemy.exc.IntegrityError",
    }


def test_a_status_description_keeps_only_the_type() -> None:
    status = Status(StatusCode.ERROR, f"IntegrityError: {MARKER}")

    scrubbed = scrub_span(span(status=status), frozenset())

    assert scrubbed.status.status_code is StatusCode.ERROR
    assert scrubbed.status.description == "IntegrityError"


def test_validation_data_keeps_only_location_and_type() -> None:
    data = json.dumps([{"loc": ["count"], "type": "int_parsing", "input": MARKER}])

    scrubbed = scrub_span(span({"exception.logfire.data": data}), frozenset())

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

    scrubbed = scrub_span(span(attributes), frozenset())

    assert attributes_of(scrubbed) == {
        "http.url": "http://api/search",
        "http.target": "/search",
        "logfire.msg": "GET /search",
    }


def test_an_ordinary_message_with_a_question_mark_is_kept() -> None:
    attributes = {"logfire.msg": "Is it ready ? yes"}

    scrubbed = scrub_span(span(attributes), frozenset())

    assert attributes_of(scrubbed) == attributes


def test_redacts_secrets_everywhere() -> None:
    status = Status(StatusCode.ERROR, f"Error {HIDDEN}")
    event = Event("log", {"text": f"a {HIDDEN} b", "list": (HIDDEN, "x")})

    scrubbed = scrub_span(
        span({"key": f"x{HIDDEN}y"}, (event,), status, name=HIDDEN),
        frozenset({HIDDEN}),
    )

    assert HIDDEN not in everything_in(scrubbed)
    assert REDACTED in scrubbed.name
    assert attributes_of(scrubbed)["key"] == f"x{REDACTED}y"


def test_an_untouched_span_round_trips() -> None:
    original = span({"http.route": "/x", "count": 3}, (Event("log", {"a": "b"}, 15),))

    scrubbed = scrub_span(original, frozenset())

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
    assert HIDDEN not in everything_in(exported)


def record(
    msg: object,
    args: tuple[object, ...] = (),
    error: BaseException | None = None,
) -> logging.LogRecord:
    exc_info = None if error is None else (type(error), error, error.__traceback__)
    return logging.LogRecord("test", logging.ERROR, __file__, 1, msg, args, exc_info)


def formatted(log_record: logging.LogRecord) -> str:
    return logging.Formatter().format(log_record)


def test_the_filter_renders_a_database_error_safely() -> None:
    log_record = record("Unhandled %s", ("IntegrityError",), database_error())

    assert TelemetryLogFilter(frozenset()).filter(log_record)

    text = formatted(log_record)
    assert MARKER not in text
    assert "SQLSTATE 23505" in text


def test_the_filter_replaces_a_formatted_traceback() -> None:
    log_record = record("failed", error=database_error())
    _ = formatted(log_record)

    _ = TelemetryLogFilter(frozenset()).filter(log_record)

    assert MARKER not in formatted(log_record)


def test_the_filter_replaces_exception_arguments() -> None:
    log_record = record("failed: %s", (ValueError(MARKER),))

    _ = TelemetryLogFilter(frozenset()).filter(log_record)

    assert log_record.getMessage() == "failed: ValueError"


def test_the_filter_redacts_a_secret_in_the_message() -> None:
    log_record = record("connecting to %s", (HIDDEN,))

    _ = TelemetryLogFilter(frozenset({HIDDEN})).filter(log_record)

    assert formatted(log_record) == f"connecting to {REDACTED}"


def test_the_filter_redacts_a_secret_only_in_the_exception() -> None:
    log_record = record("failed", error=raised(ValueError(HIDDEN)))

    _ = TelemetryLogFilter(frozenset({HIDDEN})).filter(log_record)

    text = formatted(log_record)
    assert HIDDEN not in text
    assert REDACTED in text


def test_the_filter_twice_changes_nothing_more() -> None:
    log_filter = TelemetryLogFilter(frozenset({HIDDEN}))
    log_record = record("a %s %s", (HIDDEN, ValueError("x")), database_error())

    _ = log_filter.filter(log_record)
    once = (log_record.msg, log_record.args, log_record.exc_text)
    _ = log_filter.filter(log_record)

    assert (log_record.msg, log_record.args, log_record.exc_text) == once


def test_the_filter_leaves_an_ordinary_record_alone() -> None:
    log_record = record("job %s done", ("j-1",))

    _ = TelemetryLogFilter(frozenset({HIDDEN})).filter(log_record)

    assert log_record.msg == "job %s done"
    assert log_record.args == ("j-1",)
    assert log_record.exc_text is None


@pytest.mark.parametrize("bad_args", [("one", "two"), ()])
def test_the_filter_keeps_a_record_whose_format_is_broken(
    bad_args: tuple[object, ...],
) -> None:
    log_record = record("%s %s %s", bad_args)

    assert TelemetryLogFilter(frozenset({HIDDEN})).filter(log_record)
