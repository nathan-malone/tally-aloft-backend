"""
The minimum app version per platform, which the client-version check enforces.

It imports only the standard library, so settings can type the minimums with it
without loading the web framework in the worker and the scheduler.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple, Self, override

if TYPE_CHECKING:
    from collections.abc import Mapping

# MAJOR.MINOR.PATCH in ASCII digits. Nine digits keep int() cheap and under
# Python's digit limit, so no header value can make parsing raise.
_VERSION = re.compile(r"([0-9]{1,9})\.([0-9]{1,9})\.([0-9]{1,9})")


class ClientPlatform(StrEnum):
    """The `X-Client-Platform` values the client contract defines."""

    WEB = "web"
    IOS = "ios"
    ANDROID = "android"


class ClientVersion(NamedTuple):
    """An app's `X-Client-Version`, ordered as its three numbers are."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> Self | None:
        """Return the version `text` holds, or `None` if it isn't MAJOR.MINOR.PATCH."""
        match = _VERSION.fullmatch(text)
        if match is None:
            return None
        major, minor, patch = (int(part) for part in match.groups())
        return cls(major, minor, patch)

    @override
    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True, slots=True)
class ClientVersionPolicy:
    """
    The lowest app version each platform still supports.

    A platform without an entry has no minimum. The map is copied read-only, so
    the caller's dict can't change the policy.
    """

    minimums: Mapping[ClientPlatform, ClientVersion]

    def __post_init__(self) -> None:
        """Keep a read-only copy of the minimums."""
        object.__setattr__(self, "minimums", MappingProxyType(dict(self.minimums)))

    def minimum_for(self, platform: str) -> ClientVersion | None:
        """Return the minimum for a raw `X-Client-Platform` value, if it has one."""
        if platform not in ClientPlatform:
            return None
        return self.minimums.get(ClientPlatform(platform))
