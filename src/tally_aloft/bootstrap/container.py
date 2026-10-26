from dataclasses import dataclass
from typing import TYPE_CHECKING

from tally_aloft.application.registry import UseCases

if TYPE_CHECKING:
    from tally_aloft.bootstrap.settings import Settings


@dataclass(frozen=True, slots=True, kw_only=True)
class Overrides:
    """
    Adapter replacements, which tests pass to `create_app`.

    Each story that adds an adapter adds an optional field for it here.
    """


def build_use_cases(settings: Settings, overrides: Overrides) -> UseCases:  # noqa: ARG001  # Later stories build adapters from these.
    """Build every adapter and the `UseCases` registry from them."""
    return UseCases()
