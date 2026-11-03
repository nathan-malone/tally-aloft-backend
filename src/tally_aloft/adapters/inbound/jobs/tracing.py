"""
Task spans, which continue the trace of whatever dispatched the task.

The job queue puts the dispatcher's W3C trace context into the message's
labels (`traceparent` and `tracestate`), and a task's span continues it, so a
job shows up in the trace of the request or sweep that recorded it. A message
without that context, such as the scheduler's, starts a new trace.
"""

from contextlib import contextmanager
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import (
    TraceContextTextMapPropagator,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

# Only the trace context travels: clients' `baggage` stays out of messages.
_PROPAGATOR = TraceContextTextMapPropagator()
_tracer = trace.get_tracer(__name__)


@contextmanager
def task_span(
    name: str,
    labels: Mapping[str, object],
    attributes: Mapping[str, str] | None = None,
) -> Generator[None]:
    """Run the block in a span named `name`, continuing the labels' trace."""
    carrier = {key: value for key, value in labels.items() if isinstance(value, str)}
    with _tracer.start_as_current_span(
        name,
        context=_PROPAGATOR.extract(carrier),
        kind=SpanKind.CONSUMER,
        attributes=attributes,
    ):
        yield
