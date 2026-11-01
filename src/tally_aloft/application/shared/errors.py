from tally_aloft.domain.shared.errors import CodedError


class ApplicationError(CodedError, abstract=True):
    """
    The base of every error that isn't about a domain's rules.

    Examples are authentication, entitlements, idempotency-key reuse, rate
    limits and unsupported clients.
    """


class IdempotencyKeyReusedError(ApplicationError):
    """A repeat of an `Idempotency-Key` that asks for a different request."""

    code = "idempotency_key_reused"

    def __init__(self) -> None:
        """Keep the one message clients see for a reused key."""
        super().__init__(
            "This Idempotency-Key was already used for a different request."
        )
