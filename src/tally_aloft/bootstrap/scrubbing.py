"""
What telemetry and logs may say about exceptions, and how secrets are hidden.

An exception reaches Logfire as its type and code locations, rendered from the
exception objects, and a database error also as its SQLSTATE and constraint;
no exception's message does. Records on stderr keep other exceptions' text,
but a database error prints as its type, SQLSTATE, constraint and frames.

Two layers do this for spans. When an exception is recorded on a span, Logfire
calls `record_exception_safely`, the only place that still has the exception
object. Every exported span then passes `ScrubbingSpanExporter`
(`span_scrubbing.py`), which enforces the same allowlist whether or not the
callback ran.

This module holds what both layers and the logs share:
- `safe_message` and `render_safely`, which describe an exception without
  its message, and `record_exception_safely`, the callback built on them;
- `secret_values` and `Redactor`, which find and hide secret values;
- `cut_validation_errors`, which keeps only a validation error's location and
  type;
- `TelemetryLogFilter`, which applies these rules to log records.
"""

import logging
import textwrap
import traceback
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, override
from urllib.parse import unquote, urlsplit

from pydantic import SecretStr

from tally_aloft.adapters.outbound.postgres.errors import (
    describe_database_error,
    is_database_error,
    qualified_name,
)

if TYPE_CHECKING:
    from logfire.types import ExceptionCallbackHelper
    from opentelemetry.util.types import AttributeValue

    from tally_aloft.bootstrap.settings import Settings

# Event attributes the callback sets. The mark tells the exporter that the
# message and stack trace were rendered from the exception objects.
SCRUBBED_MARK = "tally.exception.scrubbed"
SQLSTATE_ATTRIBUTE = "db.response.status_code"
CONSTRAINT_ATTRIBUTE = "db.constraint.name"
EXCEPTION_MESSAGE = "exception.message"
EXCEPTION_STACKTRACE = "exception.stacktrace"
REDACTED = "[redacted]"
# What a validation error keeps in telemetry. Its input, context and message
# go, because a custom validator's message can echo the input.
VALIDATION_ERROR_KEYS = ("loc", "type")


def cut_validation_errors(errors: object) -> list[dict[str, object]]:
    """
    Return validation errors with only their location and type.

    `errors` is the list pydantic, FastAPI or Logfire holds. An entry that
    isn't a mapping is dropped, and anything other than a list gives none.
    """
    if not isinstance(errors, Sequence) or isinstance(errors, str | bytes):
        return []
    entries: Sequence[object] = errors
    cut: list[dict[str, object]] = []
    for entry in entries:
        if isinstance(entry, Mapping):
            error: Mapping[object, object] = entry
            cut.append(
                {key: error[key] for key in VALIDATION_ERROR_KEYS if key in error}
            )
    return cut


def secret_values(settings: Settings) -> frozenset[str]:
    """
    Return every secret setting's value, and the password inside each URL.

    A password is included both as written and percent-decoded, because a
    client may show either.
    """
    values: set[str] = set()
    for name in type(settings).model_fields:
        value: object = getattr(settings, name)
        if not isinstance(value, SecretStr):
            continue
        text = value.get_secret_value()
        values.add(text)
        password = urlsplit(text).password
        if password is not None:
            values.update({password, unquote(password)})
    return frozenset(value for value in values if value != "")


def safe_message(error: BaseException) -> str:
    """
    Return what may be said about `error`: never its message.

    It is the description of the first database error linked from `error`,
    or else `error`'s type.
    """
    summary = describe_database_error(error)
    return qualified_name(error) if summary is None else summary.text()


def render_safely(error: BaseException) -> str:
    """
    Return `error`'s chain as `traceback` would print it, without any message.

    Each exception shows its frames, read from the code, and one line: a
    database error's type, SQLSTATE and constraint, or any other's type. No
    exception's text is read, so a value holding newlines can't slip through.
    """
    return "".join(_render(error, set()))


def _render(error: BaseException, seen: set[int]) -> list[str]:
    """Return the lines for `error`, after those of the exception it followed."""
    seen.add(id(error))
    lines: list[str] = []
    cause = error.__cause__
    context = error.__context__
    if cause is not None and id(cause) not in seen:
        lines += _render(cause, seen)
        lines.append(
            "\nThe above exception was the direct cause of the following exception:\n\n"
        )
    elif (
        context is not None
        and not error.__suppress_context__
        and id(context) not in seen
    ):
        lines += _render(context, seen)
        lines.append(
            "\nDuring handling of the above exception, another exception occurred:\n\n"
        )
    if error.__traceback__ is not None:
        lines.append("Traceback (most recent call last):\n")
        lines += traceback.format_tb(error.__traceback__)
    lines.append(f"{_describe(error)}\n")
    if isinstance(error, BaseExceptionGroup):
        for number, member in enumerate(error.exceptions, start=1):
            if id(member) in seen:
                continue
            lines.append(f"  +---------------- {number} ----------------\n")
            lines.append(textwrap.indent("".join(_render(member, seen)), "    | "))
    return lines


def _describe(error: BaseException) -> str:
    """
    Return the one line that stands for `error` in a rendered chain.

    Unlike `safe_message`, which describes the first database error anywhere
    in the chain, each line here describes its own exception, because the
    chain's other exceptions get lines of their own.
    """
    summary = describe_database_error(error) if is_database_error(error) else None
    return qualified_name(error) if summary is None else summary.text()


def record_exception_safely(helper: ExceptionCallbackHelper) -> None:
    """
    Record an exception on a span as its type and frames, never its message.

    Logfire calls this for every exception recorded on any span, and the
    event's attributes it sets replace OpenTelemetry's. A database error's
    SQLSTATE and constraint are added, and the mark tells the exporter that
    the message and stack trace are safe to keep.
    """
    error = helper.exception
    attributes = helper.event_attributes
    attributes[EXCEPTION_MESSAGE] = safe_message(error)
    attributes[EXCEPTION_STACKTRACE] = render_safely(error)
    summary = describe_database_error(error)
    if summary is not None and summary.sqlstate is not None:
        attributes[SQLSTATE_ATTRIBUTE] = summary.sqlstate
    if summary is not None and summary.constraint is not None:
        attributes[CONSTRAINT_ATTRIBUTE] = summary.constraint
    attributes[SCRUBBED_MARK] = True


class Redactor:
    """Replaces every secret value in text, the longest first."""

    def __init__(self, secrets: frozenset[str]) -> None:
        """Redact each value in `secrets`."""
        # A URL holds its password, so the URL goes first, then the password.
        self._secrets = sorted(secrets, key=len, reverse=True)

    def holds_secret(self, text: str) -> bool:
        """Return whether `text` contains any secret value."""
        return any(secret in text for secret in self._secrets)

    def text(self, text: str) -> str:
        """Return `text` with every secret value replaced."""
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text

    def value(self, value: AttributeValue) -> AttributeValue:
        """Return an attribute value with every secret in its strings replaced."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            return {key: self.value(item) for key, item in value.items()}
        if isinstance(value, Sequence) and not isinstance(value, bytes):
            return tuple(self.value(item) for item in value)
        return value


class TelemetryLogFilter(logging.Filter):
    """
    Rewrite log records so they hold no database error's text and no secret.

    It never drops a record, and running it twice changes nothing, so one
    instance can sit on every handler.
    """

    def __init__(self, secrets: frozenset[str]) -> None:
        """Redact each value in `secrets`."""
        super().__init__()
        self._redactor = Redactor(secrets)

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite `record` in place, and keep it."""
        error = _logged_error(record)
        if error is not None and describe_database_error(error) is not None:
            # Formatters print `exc_text` instead of formatting the traceback.
            # It is replaced even when set, because a formatter that ran
            # before this filter caches the full traceback there.
            record.exc_text = render_safely(error)
        if isinstance(record.msg, BaseException):
            record.msg = safe_message(record.msg)
        record.args = _safe_arguments(record.args)
        self._redact_message(record)
        self._redact_exception(record)
        return True

    def _redact_message(self, record: logging.LogRecord) -> None:
        message = _formatted(record)
        if message is None:
            # The handler prints the message and arguments when it reports a
            # bad format. Each is redacted on its own, keeping the argument
            # count, so formatting still fails, unless the secret held the
            # bad conversion. A message that isn't a string becomes its shown
            # text, because the handler would otherwise call `str` on it again.
            if isinstance(record.msg, str):
                record.msg = self._redactor.text(record.msg)
            else:
                record.msg = self._shown(record.msg)
            record.args = self._redact_arguments(record.args)
            message = _formatted(record)
        if message is None:
            # The handler formats again, and prints what an argument's `str`
            # raises, so such an argument becomes its shown text. The record
            # may then format, so its text is checked like any other.
            record.args = self._text_arguments(record.args)
            message = _formatted(record)
        if message is None:
            return
        if self._redactor.holds_secret(message):
            record.msg = self._redactor.text(message)
            record.args = ()

    def _redact_arguments(
        self, arguments: tuple[object, ...] | Mapping[str, object] | None
    ) -> tuple[object, ...] | Mapping[str, object] | None:
        if arguments is None:
            return None
        if isinstance(arguments, Mapping):
            return {
                key: self._redact_argument(value) for key, value in arguments.items()
            }
        return tuple(self._redact_argument(value) for value in arguments)

    def _redact_argument(self, value: object) -> object:
        """Return `value`, or its redacted `repr` if that shows a secret."""
        if isinstance(value, str):
            return self._redactor.text(value)
        try:
            shown = repr(value)
        except Exception:  # noqa: BLE001  # The filter must never raise.
            return value
        return (
            self._redactor.text(shown) if self._redactor.holds_secret(shown) else value
        )

    def _text_arguments(
        self, arguments: tuple[object, ...] | Mapping[str, object] | None
    ) -> tuple[object, ...] | Mapping[str, object] | None:
        if arguments is None:
            return None
        if isinstance(arguments, Mapping):
            return {key: self._text(value) for key, value in arguments.items()}
        return tuple(self._text(value) for value in arguments)

    def _text(self, value: object) -> object:
        """
        Return `value`, unless its `str` raises or shows a secret.

        Then its shown text or its redacted `str` takes its place, so the
        other arguments keep their types and a bad format fails as before.
        """
        try:
            text = str(value)
        except Exception:  # noqa: BLE001  # The filter must never raise.
            return self._shown(value)
        return self._redactor.text(text) if self._redactor.holds_secret(text) else value

    def _shown(self, value: object) -> str:
        """Return `value`'s redacted `repr`, or its type's name if that fails."""
        try:
            return self._redactor.text(repr(value))
        except Exception:  # noqa: BLE001  # The filter must never raise.
            return qualified_name(value)

    def _redact_exception(self, record: logging.LogRecord) -> None:
        text = record.exc_text
        if text is None and record.exc_info is not None:
            text = logging.Formatter().formatException(record.exc_info)
        if text is not None and self._redactor.holds_secret(text):
            record.exc_text = self._redactor.text(text)


def _formatted(record: logging.LogRecord) -> str | None:
    """Return a record's message, or `None` if its format is broken."""
    try:
        return record.getMessage()
    except Exception:  # noqa: BLE001  # A bad format is the handler's to report.
        return None


def _logged_error(record: logging.LogRecord) -> BaseException | None:
    """Return the exception a record carries in `exc_info`, if any."""
    if record.exc_info is None:
        return None
    return record.exc_info[1]


def _safe_arguments(
    arguments: tuple[object, ...] | Mapping[str, object] | None,
) -> tuple[object, ...] | Mapping[str, object] | None:
    """Replace each exception among a record's arguments by its safe message."""
    if arguments is None:
        return None
    if isinstance(arguments, Mapping):
        return {key: _safe_argument(value) for key, value in arguments.items()}
    return tuple(_safe_argument(value) for value in arguments)


def _safe_argument(value: object) -> object:
    return safe_message(value) if isinstance(value, BaseException) else value
