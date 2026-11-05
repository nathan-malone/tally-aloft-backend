from asyncpg.exceptions import InterfaceError
from sqlalchemy.exc import IntegrityError

from tally_aloft.adapters.outbound.postgres.errors import (
    DatabaseErrorSummary,
    describe_database_error,
    is_database_error,
    linked_errors,
    qualified_name,
)
from tests.support.database_errors import MARKER, unique_violation


def wrapped(error: BaseException) -> IntegrityError:
    """Return SQLAlchemy's error around `error`, linked as SQLAlchemy links it."""
    wrapper = IntegrityError("INSERT INTO t VALUES ($1)", None, error)
    wrapper.__cause__ = error
    return wrapper


class ForeignError(Exception):
    """An error from a module whose name merely contains `sqlalchemy`."""


ForeignError.__module__ = "mypkg.sqlalchemy_helpers"


def test_describes_a_unique_violation() -> None:
    error = unique_violation()

    assert describe_database_error(error) == DatabaseErrorSummary(
        qualified_name(error), "23505", "uq_key"
    )


def test_describes_sqlalchemys_error_by_the_asyncpg_error_behind_it() -> None:
    wrapper = wrapped(unique_violation())

    assert describe_database_error(wrapper) == DatabaseErrorSummary(
        "sqlalchemy.exc.IntegrityError", "23505", "uq_key"
    )


def test_finds_a_database_error_through_the_context() -> None:
    database_error = unique_violation()
    error = RuntimeError("handling it")
    error.__context__ = database_error

    summary = describe_database_error(error)

    assert summary is not None
    assert summary.sqlstate == "23505"


def test_finds_a_database_error_in_an_exception_group() -> None:
    group = ExceptionGroup("tasks failed", [ValueError("other"), unique_violation()])

    summary = describe_database_error(group)

    assert summary is not None
    assert summary.constraint == "uq_key"


def test_describes_nothing_for_another_error() -> None:
    assert describe_database_error(ValueError(MARKER)) is None


def test_a_cyclic_chain_ends() -> None:
    first = ValueError("first")
    second = ValueError("second")
    first.__context__ = second
    second.__context__ = first

    assert list(linked_errors(first)) == [first, second]


def test_a_client_side_error_has_no_sqlstate() -> None:
    error = InterfaceError(f"cannot use {MARKER!r} here")

    assert describe_database_error(error) == DatabaseErrorSummary(
        "asyncpg.exceptions._base.InterfaceError", None, None
    )


def test_recognises_database_errors_by_module() -> None:
    assert is_database_error(unique_violation())
    assert is_database_error(wrapped(unique_violation()))
    assert not is_database_error(ValueError())
    assert not is_database_error(ForeignError())


def test_summary_text_leaves_out_unknown_parts() -> None:
    assert DatabaseErrorSummary("E", "23505", "uq").text() == (
        "E (SQLSTATE 23505, constraint uq)"
    )
    assert DatabaseErrorSummary("E", "22P02", None).text() == "E (SQLSTATE 22P02)"
    assert DatabaseErrorSummary("E", None, None).text() == "E"


def test_qualified_name_drops_the_builtins_module() -> None:
    assert qualified_name(ValueError()) == "ValueError"
    assert qualified_name(IntegrityError("", None, ValueError())) == (
        "sqlalchemy.exc.IntegrityError"
    )
