from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tally_aloft.adapters.outbound.postgres.health import PostgresCheck
from tally_aloft.application.registry import UseCases
from tally_aloft.application.shared.check_readiness import CheckReadiness

if TYPE_CHECKING:
    from collections.abc import Mapping
    from http import HTTPStatus

    from tally_aloft.application.ports.health import DependencyCheck
    from tally_aloft.bootstrap.resources import Resources
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
    # Readiness checks in place of the real ones. `None` builds the real ones;
    # `()` means no checks at all, whatever later stories add.
    dependency_checks: tuple[DependencyCheck, ...] | None = None


def build_use_cases(
    settings: Settings,  # noqa: ARG001  # Later stories build adapters from settings.
    resources: Resources,
    overrides: Overrides,
) -> UseCases:
    """Build every adapter, on the opened `resources`, and the `UseCases` registry."""
    checks = (
        _dependency_checks(resources)
        if overrides.dependency_checks is None
        else overrides.dependency_checks
    )
    return UseCases(check_readiness=CheckReadiness(checks))


def _dependency_checks(resources: Resources) -> tuple[DependencyCheck, ...]:
    """Return the real readiness checks, one per data store."""
    return (PostgresCheck(resources.engine),)
