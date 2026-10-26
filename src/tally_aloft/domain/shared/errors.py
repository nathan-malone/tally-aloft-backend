from typing import ClassVar


class CodedError(Exception):
    """
    An error with a stable, machine-readable code that clients branch on.

    Every concrete subclass sets its own `code`, so no two errors share one by
    accident. A base that only groups other errors passes `abstract=True` and
    needs none.
    """

    code: ClassVar[str]

    def __init_subclass__(cls, *, abstract: bool = False) -> None:
        """Require a concrete subclass to set its own string `code`."""
        super().__init_subclass__()
        if not abstract and not isinstance(cls.__dict__.get("code"), str):
            msg = f"{cls.__qualname__} must set its own string `code`"
            raise TypeError(msg)


class DomainError(CodedError, abstract=True):
    """The base of every error that a domain rule raises."""
