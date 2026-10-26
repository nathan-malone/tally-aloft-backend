from tally_aloft.domain.shared.errors import CodedError


class ApplicationError(CodedError, abstract=True):
    """
    The base of every error that isn't about a domain's rules.

    Examples are authentication, entitlements, idempotency-key reuse, rate
    limits and unsupported clients.
    """
