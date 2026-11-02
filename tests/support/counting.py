from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class CountingFactory[U]:
    """A unit-of-work factory that counts the units of work it makes."""

    def __init__(self, make: Callable[[], U]) -> None:
        self._make = make
        self.made = 0

    def __call__(self) -> U:
        self.made += 1
        return self._make()
