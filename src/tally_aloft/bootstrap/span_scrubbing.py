"""
The export pipeline's rules: what an exported span may hold.

Every exporter the processes use is wrapped in a `ScrubbingSpanExporter`,
tests' included. It cuts exception events to the allowlist that
`scrubbing.record_exception_safely` writes, cuts status descriptions to the
exception's type, keeps only the location and type of validation errors, drops
the client's address and the query string, and redacts secret values.
"""

import json
import re
from typing import TYPE_CHECKING, override

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Link, Status

from tally_aloft.bootstrap.scrubbing import (
    CONSTRAINT_ATTRIBUTE,
    EXCEPTION_MESSAGE,
    EXCEPTION_STACKTRACE,
    SCRUBBED_MARK,
    SQLSTATE_ATTRIBUTE,
    Redactor,
    cut_validation_errors,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from opentelemetry.util.types import Attributes, AttributeValue

_EXCEPTION_EVENT = "exception"
_EXCEPTION_TYPE = "exception.type"
_EXCEPTION_ESCAPED = "exception.escaped"

# Logfire's: a hash of the stack trace that groups issues, and the errors of a
# pydantic `ValidationError`, inputs included, as JSON.
_FINGERPRINT = "logfire.exception.fingerprint"
_VALIDATION_DATA = "exception.logfire.data"
_LOGFIRE_MESSAGE = "logfire.msg"

# What an exception event keeps once the callback has rendered it, and what it
# keeps when the callback didn't run.
_RENDERED_EVENT_KEYS = frozenset(
    {
        _EXCEPTION_TYPE,
        EXCEPTION_MESSAGE,
        EXCEPTION_STACKTRACE,
        _EXCEPTION_ESCAPED,
        SQLSTATE_ATTRIBUTE,
        CONSTRAINT_ATTRIBUTE,
        _FINGERPRINT,
        SCRUBBED_MARK,
    }
)
_UNRENDERED_EVENT_KEYS = frozenset(
    {_EXCEPTION_TYPE, _EXCEPTION_ESCAPED, SQLSTATE_ATTRIBUTE, CONSTRAINT_ATTRIBUTE}
)
# The client's address, under the old and the stable semantic conventions.
_CLIENT_ADDRESS_KEYS = frozenset(
    {
        "client.address",
        "client.port",
        "net.peer.ip",
        "net.peer.port",
        "http.client_ip",
        "network.peer.address",
        "network.peer.port",
    }
)
_QUERY_KEY = "url.query"
_URL_KEYS = ("http.url", "url.full", "http.target")
# Present on HTTP spans, whose message Logfire builds from the method and the
# target, with the query values appended after ` ? `.
_HTTP_KEYS = ("http.route", "http.target", "url.path")
# Headers, which OpenTelemetry records only when its capture variables are set.
# `X-Forwarded-For` alone would hold the client's address.
_HEADER_PREFIXES = ("http.request.header.", "http.response.header.")
# What a status description may keep: an exception type's name.
_TYPE_NAME = re.compile(r"[A-Za-z_][\w.]*")


class ScrubbingSpanExporter(SpanExporter):
    """
    Pass spans to `inner` once they hold nothing Logfire may not receive.

    Every exporter the processes use is wrapped in one, tests' included, so
    whatever is exported has been through the same rules.
    """

    def __init__(self, inner: SpanExporter, secrets: frozenset[str]) -> None:
        """Wrap `inner`, redacting each value in `secrets`."""
        self._inner = inner
        self._redactor = Redactor(secrets)

    @override
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Scrub `spans`, then export them through `inner`."""
        return self._inner.export([scrub_span(span, self._redactor) for span in spans])

    @override
    def shutdown(self) -> None:
        """Shut `inner` down."""
        self._inner.shutdown()

    @override
    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Flush `inner`."""
        return self._inner.force_flush(timeout_millis)


def scrub_span(span: ReadableSpan, redactor: Redactor) -> ReadableSpan:
    """
    Return a copy of `span` that keeps only what Logfire may receive.

    The context, parent, kind, times, scope, link contexts and the resource's
    schema URL are carried over unchanged. The name, the link attributes and
    the resource's attributes only lose their secrets. The attributes, events
    and status are rebuilt by the rules in this module, with `redactor` hiding
    the secrets.
    """
    attributes = _scrub_request(_scrub_validation_data(_as_dict(span.attributes)))
    resource = span.resource
    return ReadableSpan(
        name=redactor.text(span.name),
        context=span.context,
        parent=span.parent,
        # The OTLP encoder groups spans by resource, which compares by value,
        # so spans from one process still share one group.
        resource=Resource(
            _redact_attributes(redactor, resource.attributes), resource.schema_url
        ),
        attributes=_redact_attributes(redactor, attributes),
        events=[
            Event(
                event.name,
                _redact_attributes(redactor, _scrub_event(event)),
                event.timestamp,
            )
            for event in span.events
        ],
        links=[
            Link(link.context, _redact_attributes(redactor, link.attributes))
            for link in span.links
        ],
        kind=span.kind,
        status=_scrub_status(span.status, redactor),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


def _scrub_event(event: Event) -> dict[str, AttributeValue]:
    """Cut an exception event to the allowlist; leave any other event as it is."""
    attributes = _as_dict(event.attributes)
    if event.name != _EXCEPTION_EVENT:
        return _scrub_validation_data(attributes)
    if attributes.get(SCRUBBED_MARK) is True:
        return {k: v for k, v in attributes.items() if k in _RENDERED_EVENT_KEYS}
    # The callback didn't run, so the message and stack trace are raw.
    kept = {k: v for k, v in attributes.items() if k in _UNRENDERED_EVENT_KEYS}
    kept[EXCEPTION_MESSAGE] = attributes.get(_EXCEPTION_TYPE, "")
    return kept


def _scrub_status(status: Status, redactor: Redactor) -> Status:
    """
    Cut a status description to the exception type it starts with, or drop it.

    Logfire and OpenTelemetry both describe an exception's status as
    `<Name>: <message>`, so this keeps the type. A description that doesn't
    start with a type name could be anything, so none is kept.
    """
    description = status.description
    if description is None:
        return status
    name = description.partition(":")[0].strip()
    if _TYPE_NAME.fullmatch(name) is None:
        return Status(status.status_code)
    return Status(status.status_code, redactor.text(name))


def _scrub_validation_data(
    attributes: dict[str, AttributeValue],
) -> dict[str, AttributeValue]:
    """Keep only each validation error's location and type, never its input."""
    data = attributes.get(_VALIDATION_DATA)
    if data is None:
        return attributes
    scrubbed = {k: v for k, v in attributes.items() if k != _VALIDATION_DATA}
    errors = _validation_errors(data)
    if errors is not None:
        scrubbed[_VALIDATION_DATA] = json.dumps(errors)
    return scrubbed


def _validation_errors(data: AttributeValue) -> list[dict[str, object]] | None:
    """Return the errors in Logfire's validation JSON, cut down, or `None`."""
    if not isinstance(data, str):
        return None
    try:
        parsed: object = json.loads(data)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    return cut_validation_errors(parsed)


def _scrub_request(attributes: dict[str, AttributeValue]) -> dict[str, AttributeValue]:
    """Drop the client's address, the query string and headers; keep the path."""
    scrubbed = {
        key: value
        for key, value in attributes.items()
        if key not in _CLIENT_ADDRESS_KEYS
        and key != _QUERY_KEY
        and not key.startswith(_HEADER_PREFIXES)
    }
    for key in _URL_KEYS:
        value = scrubbed.get(key)
        if isinstance(value, str):
            scrubbed[key] = value.partition("?")[0]
    message = scrubbed.get(_LOGFIRE_MESSAGE)
    if isinstance(message, str) and any(key in scrubbed for key in _HTTP_KEYS):
        scrubbed[_LOGFIRE_MESSAGE] = message.partition("?")[0].rstrip()
    return scrubbed


def _redact_attributes(
    redactor: Redactor, attributes: Attributes
) -> dict[str, AttributeValue]:
    """Return `attributes` with every secret in their values replaced."""
    return {key: redactor.value(value) for key, value in _as_dict(attributes).items()}


def _as_dict(attributes: Attributes) -> dict[str, AttributeValue]:
    """Return a span's, event's, link's or resource's attributes as a new dict."""
    return {} if attributes is None else dict(attributes)
