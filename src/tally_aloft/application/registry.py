from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class UseCases:
    """
    Every use case that inbound adapters may call.

    Bootstrap builds it, and inbound adapters read it. Use cases never import it.
    """
