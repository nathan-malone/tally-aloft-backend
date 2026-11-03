"""
Recognising database errors, and describing one without its message.

asyncpg and SQLAlchemy put the values a statement failed on into their errors'
messages, such as `DETAIL: Key (key)=(...) already exists`, so telemetry and
logs describe a database error by its type, SQLSTATE and constraint instead.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

# An exception is a database error when its class comes from one of these
# packages, so a new asyncpg or SQLAlchemy subclass is covered without a list.
# That includes asyncpg's client-side `DataError`, which echoes argument values.
_DATABASE_PACKAGES = ("asyncpg", "sqlalchemy")


@dataclass(frozen=True, slots=True)
class DatabaseErrorSummary:
    """A database error, as far as it can be told without its message."""

    type_name: str
    sqlstate: str | None
    constraint: str | None

    def text(self) -> str:
        """Return the type, with the SQLSTATE and constraint when known."""
        details = [
            label
            for label in (
                None if self.sqlstate is None else f"SQLSTATE {self.sqlstate}",
                None if self.constraint is None else f"constraint {self.constraint}",
            )
            if label is not None
        ]
        if len(details) == 0:
            return self.type_name
        return f"{self.type_name} ({', '.join(details)})"


def qualified_name(error: BaseException) -> str:
    """Return the error's type as OpenTelemetry records it: `module.qualname`."""
    module = type(error).__module__
    name = type(error).__qualname__
    return name if module in {"", "builtins"} else f"{module}.{name}"


def is_database_error(error: BaseException) -> bool:
    """Return whether `error` comes from asyncpg or SQLAlchemy."""
    module = type(error).__module__
    return any(
        module == package or module.startswith(f"{package}.")
        for package in _DATABASE_PACKAGES
    )


def linked_errors(error: BaseException) -> Iterator[BaseException]:
    """
    Yield `error` and every exception linked from it, each once, depth first.

    The links are `__cause__`, `__context__`, SQLAlchemy's `orig` and an
    exception group's members. A cyclic chain ends.
    """
    seen: set[int] = set()
    pending = [error]
    while len(pending) > 0:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        linked: list[object] = [
            current.__cause__,
            current.__context__,
            getattr(current, "orig", None),
        ]
        if isinstance(current, BaseExceptionGroup):
            linked.extend(current.exceptions)
        pending.extend(
            link for link in reversed(linked) if isinstance(link, BaseException)
        )


def describe_database_error(error: BaseException) -> DatabaseErrorSummary | None:
    """
    Describe the first database error linked from `error`, or return `None`.

    The SQLSTATE and constraint come from the first asyncpg `PostgresError`
    linked from that error; SQLAlchemy's errors wrap one. Either is `None`
    when unknown, as for a client-side error that never reached the server.
    """
    for found in linked_errors(error):
        if is_database_error(found):
            sqlstate, constraint = _server_fields(found)
            return DatabaseErrorSummary(qualified_name(found), sqlstate, constraint)
    return None


def _server_fields(error: BaseException) -> tuple[str | None, str | None]:
    """
    Return the SQLSTATE and constraint of the server error behind `error`.

    asyncpg's own error is preferred: SQLAlchemy's adapted error copies its
    `sqlstate` but not its `constraint_name`.
    """
    candidates = [
        found
        for found in linked_errors(error)
        if is_database_error(found)
        and isinstance(getattr(found, "sqlstate", None), str)
    ]
    candidates.sort(key=lambda found: not type(found).__module__.startswith("asyncpg"))
    if len(candidates) == 0:
        return None, None
    sqlstate = getattr(candidates[0], "sqlstate", None)
    constraint = getattr(candidates[0], "constraint_name", None)
    return (
        sqlstate if isinstance(sqlstate, str) else None,
        constraint if isinstance(constraint, str) else None,
    )
