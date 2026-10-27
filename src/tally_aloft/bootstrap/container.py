from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tally_aloft.application.registry import UseCases

if TYPE_CHECKING:
    from collections.abc import Mapping
    from http import HTTPStatus

    from tally_aloft.bootstrap.settings import Settings
    from tally_aloft.domain.shared.errors import CodedError


@dataclass(frozen=True, slots=True, kw_only=True)
class Overrides:
    """
    Test replacements for adapters and HTTP configuration, passed to `create_app`.

    Each story that adds an adapter adds an optional field for it here.
    """

    # Statuses for test-only errors, merged over the production error table.
    error_statuses: Mapping[type[CodedError], HTTPStatus] = field(default_factory=dict)


def build_use_cases(settings: Settings, overrides: Overrides) -> UseCases:  # noqa: ARG001  # Later stories build adapters from these.
    """Build every adapter and the `UseCases` registry from them."""
    return UseCases()
