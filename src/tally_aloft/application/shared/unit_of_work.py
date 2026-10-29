from typing import TYPE_CHECKING, Protocol, Self

if TYPE_CHECKING:
    from types import TracebackType


class SharedUnitOfWork(Protocol):
    """
    One database transaction, entered with `async with self.uow() as uow:`.

    It commits when the block exits cleanly and rolls back when it raises.
    Each domain's unit-of-work Protocol extends this one with its repositories,
    and the shared repositories join it as they're added. Nothing inside one
    runs concurrently, because a database session can't be shared by tasks.
    """

    async def __aenter__(self) -> Self:
        """Begin the transaction."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Commit on a clean exit, or roll back on an exception."""
        ...
