import pytest

from tally_aloft.application.shared.errors import ApplicationError
from tally_aloft.domain.shared.errors import CodedError, DomainError, is_concrete


class FlightNotFoundError(DomainError):
    code = "flight_not_found"


def test_concrete_domain_error_has_its_code() -> None:
    assert FlightNotFoundError.code == "flight_not_found"
    assert FlightNotFoundError("No such flight.").code == "flight_not_found"


def test_error_keeps_its_client_message() -> None:
    error = FlightNotFoundError("No such flight.")

    assert error.message == "No such flight."
    assert str(error) == "No such flight."


def test_domain_error_without_a_code_is_rejected() -> None:
    with pytest.raises(TypeError, match="must set its own string `code`"):

        class MissingCodeError(DomainError):
            pass


def test_child_of_a_concrete_error_needs_its_own_code() -> None:
    with pytest.raises(TypeError, match="must set its own string `code`"):

        class ChildError(FlightNotFoundError):
            pass


def test_grouping_base_needs_no_code_but_its_children_do() -> None:
    class LogbookError(DomainError, abstract=True):
        pass

    class EntryLockedError(LogbookError):
        code = "entry_locked"

    assert EntryLockedError.code == "entry_locked"
    assert issubclass(EntryLockedError, DomainError)


def test_only_classes_with_their_own_code_are_concrete() -> None:
    class LogbookError(DomainError, abstract=True):
        pass

    assert is_concrete(FlightNotFoundError)
    assert not is_concrete(LogbookError)
    assert not is_concrete(DomainError)
    assert not is_concrete(ApplicationError)
    assert not is_concrete(CodedError)
