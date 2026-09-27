"""
Domain exceptions.

These represent business-rule violations and are raised from the service /
repository layers. They know nothing about HTTP — the API layer (exception
handlers in `app.main`) is responsible for translating each of these into an
appropriate status code and a consistent error envelope. This keeps the
business logic testable in isolation from the web framework.
"""


class DomainError(Exception):
    """Base class for all business-rule errors."""

    code: str = "domain_error"
    http_status: int = 400

    def __init__(self, message: str | None = None):
        super().__init__(message or self.code)
        self.message = message or self.code


class AccountNotFoundError(DomainError):
    code = "account_not_found"
    http_status = 404

    def __init__(self, account_id: str):
        super().__init__(f"Account '{account_id}' was not found.")
        self.account_id = account_id


class InvalidAccountDataError(DomainError):
    code = "invalid_account_data"
    http_status = 422


class InsufficientFundsError(DomainError):
    code = "insufficient_funds"
    http_status = 422

    def __init__(self, account_id: str):
        super().__init__(f"Account '{account_id}' has insufficient funds for this transfer.")
        self.account_id = account_id


class SameAccountTransferError(DomainError):
    code = "same_account_transfer"
    http_status = 422

    def __init__(self):
        super().__init__("Cannot transfer money from an account to itself.")


class CurrencyMismatchError(DomainError):
    code = "currency_mismatch"
    http_status = 422


class InvalidAmountError(DomainError):
    code = "invalid_amount"
    http_status = 422


class AccountInactiveError(DomainError):
    code = "account_inactive"
    http_status = 422

    def __init__(self, account_id: str):
        super().__init__(f"Account '{account_id}' is not active.")
        self.account_id = account_id


class IdempotencyKeyConflictError(DomainError):
    """
    Raised when the same Idempotency-Key is reused with a *different*
    request payload. Per common idempotency semantics (Stripe-style), this
    is a client error distinct from a legitimate replay.
    """

    code = "idempotency_key_conflict"
    http_status = 409


class ConcurrentModificationError(DomainError):
    """Raised when an optimistic-locking retry budget is exhausted."""

    code = "concurrent_modification"
    http_status = 409
