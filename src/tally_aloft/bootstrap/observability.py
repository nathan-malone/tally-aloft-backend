"""
Logfire: what the API and the workers trace, and where the spans go.

Logfire never exports by itself. Every span passes `ScrubbingSpanExporter`,
and only with `TALLY_LOGFIRE_TOKEN` does one go to Logfire's OTLP endpoint, so
no `LOGFIRE_TOKEN` or Logfire credentials file can send anything. Logfire's
own scrubbing callback can't do this job: it skips exception messages, query
strings and URLs (see tech-stack.md, Observability).
"""

import logging
import sys
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

import logfire
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased

from tally_aloft.bootstrap.scrubbing import (
    VALIDATION_ERROR_KEYS,
    TelemetryLogFilter,
    record_exception_safely,
    secret_values,
)
from tally_aloft.bootstrap.span_scrubbing import ScrubbingSpanExporter

if TYPE_CHECKING:
    from fastapi import FastAPI
    from opentelemetry.sdk.trace import SpanProcessor
    from opentelemetry.sdk.trace.export import SpanExporter
    from redis.asyncio import Redis

    from tally_aloft.bootstrap.settings import Settings

API_SERVICE = "tally-aloft-api"
# The scheduler imports the worker module, so it reports as the worker.
WORKER_SERVICE = "tally-aloft-worker"

# Every request is recorded, whatever the client's own sampling decision: the
# default sampler would drop a request whose `traceparent` ends in `-00`.
_SAMPLER = ParentBased(ALWAYS_ON, remote_parent_not_sampled=ALWAYS_ON)
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# Taskiq logs every task it runs at INFO, which the task spans already show.
_QUIET_LOGGERS = ("taskiq",)


def configure_observability(
    settings: Settings,
    service: str,
    *,
    exporters: Sequence[SpanExporter] = (),
) -> None:
    """
    Configure Logfire and logging for this process, and instrument asyncpg.

    Each exporter in `exporters`, which tests pass, gets every span at once,
    through the same scrubbing as Logfire's. Every argument that changes what
    is recorded or exported is passed here, so no `LOGFIRE_*` variable changes
    it. Calling it again reconfigures Logfire; asyncpg is instrumented once
    per process.
    """
    secrets = secret_values(settings)
    processors: list[SpanProcessor] = [
        SimpleSpanProcessor(ScrubbingSpanExporter(exporter, secrets))
        for exporter in exporters
    ]
    if settings.logfire_token is not None:
        logfire_exporter = _logfire_exporter(settings.logfire_token.get_secret_value())
        processors.append(
            BatchSpanProcessor(ScrubbingSpanExporter(logfire_exporter, secrets))
        )
    _ = logfire.configure(
        send_to_logfire=False,
        service_name=service,
        environment=settings.environment.value,
        console=False,
        metrics=False,
        distributed_tracing=True,
        # Clients' Sentry `baggage` stays out of span attributes.
        add_baggage_to_attributes=False,
        scrubbing=logfire.ScrubbingOptions(),
        min_level="trace",
        inspect_arguments=False,
        sampling=logfire.SamplingOptions(head=_SAMPLER),
        additional_span_processors=processors,
        advanced=logfire.AdvancedOptions(exception_callback=record_exception_safely),
    )
    _configure_logging(TelemetryLogFilter(secrets))
    # SQLAlchemy isn't instrumented: its spans would duplicate asyncpg's.
    if not AsyncPGInstrumentor().is_instrumented_by_opentelemetry:
        logfire.instrument_asyncpg()


def _configure_logging(log_filter: TelemetryLogFilter) -> None:
    """
    Send every record at INFO or above to stderr and to Logfire, through `log_filter`.

    The root logger's other handlers are removed, such as the one Taskiq's
    `basicConfig` adds in the scheduler and in `spawn`-started workers, so no
    record reaches a handler without the filter. Logfire's handler would print
    a record logged during export a second time, so its fallback drops it.
    """
    to_stderr = logging.StreamHandler(sys.stderr)
    to_stderr.setFormatter(logging.Formatter(_LOG_FORMAT))
    to_logfire = logfire.LogfireLoggingHandler(fallback=logging.NullHandler())
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in (to_stderr, to_logfire):
        handler.addFilter(log_filter)
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def instrument_app(app: FastAPI) -> FastAPI:
    """Trace `app`'s requests, without endpoint argument values."""
    _ = logfire.instrument_fastapi(app, request_attributes_mapper=request_attributes)
    return app


def instrument_cache_client(client: Redis) -> None:
    """
    Trace the cache's Valkey commands, as `SET ? ?`, without their values.

    Only the cache's client is traced. The broker's isn't, because its polling
    would make a span every 2 seconds in each worker process.
    """
    RedisInstrumentor.instrument_client(client)


def request_attributes(
    _request: object, attributes: Mapping[str, object]
) -> dict[str, object]:
    """
    Return what a request span records of its endpoint's arguments.

    No argument value is kept. Validation errors keep their location and type,
    so the span still shows a validation failure. The result is never empty,
    because Logfire sets a request span with no attributes to debug level.
    """
    errors = attributes.get("errors")
    kept: list[dict[str, object]] = []
    if isinstance(errors, Sequence) and not isinstance(errors, str):
        items: Sequence[object] = errors
        for item in items:
            if isinstance(item, Mapping):
                error: Mapping[object, object] = item
                kept.append(
                    {key: error[key] for key in VALIDATION_ERROR_KEYS if key in error}
                )
    return {"errors": kept}


def _logfire_exporter(token: str) -> OTLPSpanExporter:
    """Return the exporter to Logfire's OTLP endpoint for `token`'s region."""
    base_url = logfire.AdvancedOptions().generate_base_url(token)
    return OTLPSpanExporter(
        endpoint=f"{base_url}/v1/traces",
        headers={"Authorization": token},
        compression=Compression.Gzip,
    )
