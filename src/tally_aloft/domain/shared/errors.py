from typing import ClassVar


class CodedError(Exception):
    """
    An error with a stable, machine-readable code that clients branch on.

    Every concrete subclass sets its own `code`, so no two errors share one by
    accident. A base that only groups other errors passes `abstract=True` and
    needs none.

    The message is shown to clients as written, so it must hold no internal
    detail and no other pilot's data.
    """

    code: ClassVar[str]

    def __init__(self, message: str) -> None:
        """Keep the client-facing `message`."""
        super().__init__(message)
        self.message = message

    def __init_subclass__(cls, *, abstract: bool = False) -> None:
        """Require a concrete subclass to set its own string `code`."""
        super().__init_subclass__()
        if not abstract and not is_concrete(cls):
            msg = f"{cls.__qualname__} must set its own string `code`"
            raise TypeError(msg)


def is_concrete(error_class: type[CodedError]) -> bool:
    """Return whether `error_class` sets its own string `code`."""
    return isinstance(error_class.__dict__.get("code"), str)


class DomainError(CodedError, abstract=True):
    """The base of every error that a domain rule raises."""
