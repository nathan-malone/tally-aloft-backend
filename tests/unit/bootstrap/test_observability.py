from typing import TYPE_CHECKING, ClassVar, NoReturn, override

import logfire
import pytest
import requests.adapters
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from tally_aloft.bootstrap import observability
from tally_aloft.bootstrap.observability import (
    API_SERVICE,
    configure_observability,
    request_attributes,
)
from tally_aloft.bootstrap.settings import Settings
from tests.support.telemetry import attributes_of

if TYPE_CHECKING:
    from collections.abc import Sequence

    from opentelemetry.sdk.trace import ReadableSpan

# A Logfire token's shape, so Logfire reads its region without a warning.
TOKEN = "pylf_v1_us_token-marker-606"  # noqa: S105  # A test value.


class RecordingExporter(SpanExporter):
    """Stands in for the OTLP exporter, recording what it would send."""

    made: ClassVar[list[RecordingExporter]] = []

    def __init__(
        self, *, endpoint: str, headers: dict[str, str], **_options: object
    ) -> None:
        self.endpoint = endpoint
        self.headers = headers
        self.spans: list[ReadableSpan] = []
        RecordingExporter.made.append(self)

    @override
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.spans += spans
        return SpanExportResult.SUCCESS


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record, and refuse, any HTTP request a test's code tries to send."""
    sent: list[str] = []

    def refuse(
        _adapter: object, request: requests.PreparedRequest, **_kwargs: object
    ) -> NoReturn:
        sent.append(str(request.url))
        pytest.fail("telemetry tried to reach the network")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", refuse)
    return sent


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[RecordingExporter]:
    """Replace the OTLP exporter, and return each one made."""
    RecordingExporter.made = []
    monkeypatch.setattr(observability, "OTLPSpanExporter", RecordingExporter)
    return RecordingExporter.made


@pytest.mark.usefixtures("isolated_telemetry")
def test_no_token_sends_nothing_even_with_logfires_own_token(
    monkeypatch: pytest.MonkeyPatch,
    no_network: list[str],
    recorded: list[RecordingExporter],
) -> None:
    monkeypatch.setenv("LOGFIRE_TOKEN", TOKEN)

    configure_observability(Settings.for_tests(), API_SERVICE)
    with logfire.span("work"):
        pass
    assert logfire.force_flush()

    assert recorded == []
    assert no_network == []


@pytest.mark.usefixtures("isolated_telemetry")
def test_a_token_sends_scrubbed_spans_to_logfire(
    recorded: list[RecordingExporter],
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_observability(Settings.for_tests(logfire_token=TOKEN), API_SERVICE)
    with logfire.span("connecting {target}", target=f"with {TOKEN}"):
        pass
    assert logfire.force_flush()

    [exporter] = recorded
    assert exporter.endpoint == "https://logfire-us.pydantic.dev/v1/traces"
    assert exporter.headers == {"Authorization": TOKEN}
    [span] = exporter.spans
    assert TOKEN not in repr((span.name, attributes_of(span)))
    output = capsys.readouterr()
    assert TOKEN not in output.out
    assert TOKEN not in output.err


@pytest.mark.usefixtures("isolated_telemetry")
def test_asyncpg_is_instrumented_once(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_observability(Settings.for_tests(), API_SERVICE)
    calls: list[None] = []
    monkeypatch.setattr(logfire, "instrument_asyncpg", lambda: calls.append(None))

    configure_observability(Settings.for_tests(), API_SERVICE)

    assert calls == []


def test_request_attributes_drop_argument_values() -> None:
    attributes: dict[str, object] = {
        "values": {"body": {"marker": "value-marker"}},
        "errors": [],
    }

    assert request_attributes(None, attributes) == {"errors": []}


def test_request_attributes_keep_only_an_errors_location_and_type() -> None:
    error = {
        "loc": ["query", "count"],
        "type": "int_parsing",
        "msg": "Input should be a valid integer",
        "input": "input-marker",
        "ctx": {"error": "input-marker"},
    }

    assert request_attributes(None, {"values": {}, "errors": [error]}) == {
        "errors": [{"loc": ["query", "count"], "type": "int_parsing"}]
    }
