from typing import TYPE_CHECKING, override
from uuid import uuid4

from opentelemetry import baggage, context, trace
from taskiq import AsyncBroker, Context, TaskiqMessage

from tally_aloft.adapters.inbound.jobs.idempotency_cleanup import CLEANUP_TASK
from tally_aloft.adapters.inbound.jobs.sweeper import SWEEP_TASK
from tally_aloft.adapters.inbound.jobs.tracing import task_span
from tally_aloft.adapters.outbound.taskiq.job_queue import TaskiqJobQueue
from tally_aloft.application.shared.jobs.kinds import JobId
from tests.fakes.clock import FakeClock
from tests.fakes.database import FakeDatabase
from tests.fakes.unit_of_work import fake_unit_of_work_factory
from tests.support.use_cases import job_use_cases

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from taskiq.message import BrokerMessage

    from tally_aloft.adapters.inbound.jobs.tasks import TaskDefinition
    from tests.support.telemetry import Telemetry

_tracer = trace.get_tracer(__name__)


class RecordingBroker(AsyncBroker):
    """A broker that keeps each message it is asked to send."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[BrokerMessage] = []

    @override
    async def kick(self, message: BrokerMessage) -> None:
        self.sent.append(message)

    @override
    async def listen(self) -> AsyncGenerator[bytes]:
        for message in ():
            yield message


def labels_of(message: BrokerMessage) -> dict[str, object]:
    return dict(message.labels)


def trace_id_of(span: trace.Span) -> int:
    return span.get_span_context().trace_id


async def test_dispatch_sends_the_current_trace_context_without_baggage(
    telemetry: Telemetry,  # noqa: ARG001  # Spans are recorded.
) -> None:
    broker = RecordingBroker()
    token = context.attach(baggage.set_baggage("user", "baggage-marker"))
    try:
        with _tracer.start_as_current_span("request") as span:
            await TaskiqJobQueue(broker).dispatch(JobId(uuid4()), "test.echo")
    finally:
        context.detach(token)

    [message] = broker.sent
    labels = labels_of(message)
    context_of_span = span.get_span_context()
    assert labels["traceparent"] == (
        f"00-{trace.format_trace_id(context_of_span.trace_id)}"
        f"-{trace.format_span_id(context_of_span.span_id)}-01"
    )
    assert "baggage" not in labels
    assert "baggage-marker" not in repr(labels)


def test_a_task_span_continues_the_labels_trace(telemetry: Telemetry) -> None:
    parent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    with task_span("test.echo", {"traceparent": parent, "timeout": 9.0}):
        pass

    [span] = telemetry.spans()
    assert span.name == "test.echo"
    assert span.context is not None
    assert trace.format_trace_id(span.context.trace_id) == (
        "4bf92f3577b34da6a3ce929d0e0e4736"
    )
    assert span.parent is not None
    assert trace.format_span_id(span.parent.span_id) == "00f067aa0ba902b7"


def test_a_task_span_without_trace_context_starts_a_trace(
    telemetry: Telemetry,
) -> None:
    with _tracer.start_as_current_span("unrelated") as unrelated:
        pass

    with task_span("jobs.sweep", {}):
        pass

    [_, span] = telemetry.spans()
    assert span.context is not None
    assert span.parent is None
    assert span.context.trace_id != trace_id_of(unrelated)


async def run_scheduled(task: TaskDefinition) -> None:
    broker = RecordingBroker()
    message = TaskiqMessage(
        task_id="task-1", task_name=task.name, labels={}, args=[], kwargs={}
    )
    database = FakeDatabase(clock=FakeClock())
    use_cases = job_use_cases(fake_unit_of_work_factory(database))
    _ = await task.function(use_cases=use_cases, context=Context(message, broker))


async def test_scheduled_tasks_run_in_spans_named_after_them(
    telemetry: Telemetry,
) -> None:
    await run_scheduled(SWEEP_TASK)
    await run_scheduled(CLEANUP_TASK)

    # The cleanup also logs, which makes a log span of its own.
    names = {span.name for span in telemetry.spans()}
    assert {SWEEP_TASK.name, CLEANUP_TASK.name} <= names
