import pytest

from tally_aloft.application.shared.errors import ApplicationError
from tally_aloft.domain.shared.errors import DomainError


class RateLimitedError(ApplicationError):
    code = "rate_limited"


def test_concrete_application_error_has_its_code() -> None:
    assert RateLimitedError.code == "rate_limited"


def test_application_error_without_a_code_is_rejected() -> None:
    with pytest.raises(TypeError, match="must set its own string `code`"):

        class MissingCodeError(ApplicationError):
            pass


def test_application_errors_are_not_domain_errors() -> None:
    assert not issubclass(ApplicationError, DomainError)
