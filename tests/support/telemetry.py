"""
What a test sees of telemetry: the spans the scrubbing exporter let through.

The `telemetry` fixture configures Logfire with an in-memory exporter, wrapped
in the production `ScrubbingSpanExporter`, so a test checks the real rules.
"""

import io
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind

from tally_aloft.bootstrap.observability import configure_observability
from tally_aloft.bootstrap.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Generator

    from opentelemetry.sdk.trace import Event, ReadableSpan
    from opentelemetry.trace import Link

# The service name test configurations report.
TEST_SERVICE = "tally-aloft-test"
# The test client logs each request's full URL at INFO, query values included,
# which would put a test's own markers into log spans.
_QUIETED_LOGGERS = ("httpx", "httpcore")
_FASTAPI_SCOPE = "opentelemetry.instrumentation.fastapi"
_ASYNCPG_SCOPE = "opentelemetry.instrumentation.asyncpg"
_REDIS_SCOPE = "opentelemetry.instrumentation.redis"


@dataclass(frozen=True, slots=True)
class Telemetry:
    """The spans a test's code exported, with ways to search them."""

    exporter: InMemorySpanExporter
    log_output: io.StringIO

    def spans(self) -> tuple[ReadableSpan, ...]:
        """Return every span exported so far, oldest first."""
        return self.exporter.get_finished_spans()

    def request_spans(self) -> list[ReadableSpan]:
        """Return the spans of the HTTP requests the app served."""
        return [
            span
            for span in self.spans()
            if span.kind is SpanKind.SERVER and _scope(span) == _FASTAPI_SCOPE
        ]

    def query_spans(self, statement: str) -> list[ReadableSpan]:
        """Return the asyncpg spans of `statement`."""
        return [
            span
            for span in self.spans()
            if _scope(span) == _ASYNCPG_SCOPE and statement in _statement(span)
        ]

    def cache_spans(self) -> list[ReadableSpan]:
        """Return the spans of Valkey commands."""
        return [span for span in self.spans() if _scope(span) == _REDIS_SCOPE]

    def exception_events(self) -> list[dict[str, object]]:
        """Return the attributes of every exception event on any span."""
        return [
            attributes_of(event)
            for span in self.spans()
            for event in span.events
            if event.name == "exception"
        ]

    def logs(self) -> str:
        """Return what the production stderr handler has written."""
        return self.log_output.getvalue()

    def assert_absent(self, marker: str) -> None:
        """Fail if any exported span, or the stderr log, holds `marker`."""
        for span in self.spans():
            assert marker not in _everything_in(span), span.name
        assert marker not in self.logs()


def attributes_of(item: ReadableSpan | Event | Link) -> dict[str, object]:
    """Return a span's, event's or link's attributes as a dict."""
    return {} if item.attributes is None else dict(item.attributes)


def _scope(span: ReadableSpan) -> str:
    scope = span.instrumentation_scope
    return "" if scope is None else scope.name


def _statement(span: ReadableSpan) -> str:
    attributes = attributes_of(span)
    statement = attributes.get("db.statement", attributes.get("db.query.text", ""))
    return statement if isinstance(statement, str) else ""


def _everything_in(span: ReadableSpan) -> str:
    """Return every value a span holds, as one text to search."""
    parts: list[object] = [
        span.name,
        span.status.description,
        attributes_of(span),
        dict(span.resource.attributes),
    ]
    parts += [(event.name, attributes_of(event)) for event in span.events]
    parts += [attributes_of(link) for link in span.links]
    return json.dumps(parts, default=repr)


@contextmanager
def isolated_observability() -> Generator[None]:
    """
    Restore the logging and telemetry a test changed, once it ends.

    It quiets the test client's loggers while the test runs. Afterwards Logfire
    is configured with no exporter, so later tests' spans go nowhere.
    """
    root = logging.getLogger()
    root_state = (root.level, list(root.handlers))
    quieted = {name: logging.getLogger(name).level for name in _QUIETED_LOGGERS}
    for name in _QUIETED_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        yield
    finally:
        configure_observability(Settings.for_tests(), TEST_SERVICE)
        root.setLevel(root_state[0])
        root.handlers[:] = root_state[1]
        for name, level in quieted.items():
            logging.getLogger(name).setLevel(level)


def configured_telemetry(**settings: object) -> Telemetry:
    """
    Configure Logfire with a fresh in-memory exporter, and return it.

    The stderr handler `configure_observability` installs writes to a buffer
    instead, so a test reads exactly what it lets through.
    """
    exporter = InMemorySpanExporter()
    configure_observability(
        Settings.for_tests(**settings), TEST_SERVICE, exporters=(exporter,)
    )
    log_output = io.StringIO()
    for handler in logging.getLogger().handlers:
        if type(handler) is logging.StreamHandler:
            _ = handler.setStream(log_output)
    return Telemetry(exporter, log_output)
