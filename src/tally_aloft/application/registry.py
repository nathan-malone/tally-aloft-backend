from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tally_aloft.application.shared.check_readiness import CheckReadiness


@dataclass(frozen=True, slots=True, kw_only=True)
class UseCases:
    """
    Every use case that inbound adapters may call.

    Bootstrap builds it, and inbound adapters read it. Use cases never import it.
    """

    check_readiness: CheckReadiness
