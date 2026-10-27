from dataclasses import dataclass
from typing import Self
from uuid import UUID, uuid7

UUID_VERSION = 7


@dataclass(frozen=True, slots=True)
class PilotId:
    """
    The internal identifier of a pilot: a UUIDv7.

    Every domain refers to a pilot by this ID, never by the identity
    provider's user ID.
    """

    value: UUID

    def __post_init__(self) -> None:
        """Reject a UUID of any version other than 7."""
        if self.value.version != UUID_VERSION:
            msg = f"PilotId needs a version-7 UUID, not version {self.value.version}"
            raise ValueError(msg)

    @classmethod
    def new(cls) -> Self:
        """Return a new pilot ID built from a fresh UUIDv7."""
        return cls(uuid7())
