"""Application exceptions and the unified error envelope.
Error body contract::
    {"error": {"code": "...", "message": "...", "details": {}}}
This minimal base is sufficient for the Settlement execution-provider domain
errors; it carries a stable non-sensitive ``code`` and never leaks raw
provider material.
"""
from typing import Any

class AppError(Exception):
    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"

class RunNotFoundError(NotFoundError):
    code = "RUN_NOT_FOUND"

class StrategyNotFoundError(NotFoundError):
    code = "STRATEGY_NOT_FOUND"

class TokenNotFoundError(NotFoundError):
    code = "TOKEN_NOT_FOUND"

class WalletNotFoundError(NotFoundError):
    code = "WALLET_NOT_FOUND"

class TransactionNotFoundError(NotFoundError):
    code = "TRANSACTION_NOT_FOUND"

class DuplicateError(AppError):
    status_code = 409
    code = "DUPLICATE_RESOURCE"

class DuplicateTokenError(DuplicateError):
    code = "DUPLICATE_TOKEN"

class DuplicateWalletError(DuplicateError):
    code = "DUPLICATE_WALLET"

class DuplicateAssignmentError(DuplicateError):
    code = "DUPLICATE_ASSIGNMENT"

class DuplicateTransactionError(DuplicateError):
    code = "DUPLICATE_TRANSACTION"

class IllegalStateTransitionError(AppError):
    status_code = 409
    code = "ILLEGAL_STATE_TRANSITION"

class ConfigValidationError(AppError):
    status_code = 422
    code = "INVALID_CONFIG"

class MissingProviderTokenMappingError(AppError):
    status_code = 409
    code = "MISSING_PROVIDER_TOKEN_MAPPING"

class MissingProviderChainMappingError(AppError):
    status_code = 409
    code = "MISSING_PROVIDER_CHAIN_MAPPING"

class MissingProviderWalletIdError(AppError):
    status_code = 409
    code = "MISSING_PROVIDER_WALLET_ID"

class ProviderMismatchError(AppError):
    status_code = 409
    code = "PROVIDER_MISMATCH"

class ChainMismatchError(AppError):
    status_code = 409
    code = "CHAIN_MISMATCH"

class InvalidDestinationError(AppError):
    status_code = 422
    code = "INVALID_DESTINATION_ADDRESS"

class LiveTransferDisabledError(AppError):
    status_code = 403
    code = "LIVE_WALLET_TRANSFERS_DISABLED"

class UnsupportedChainError(AppError):
    status_code = 422
    code = "UNSUPPORTED_CHAIN"

class InvalidTokenAddressError(AppError):
    status_code = 422
    code = "INVALID_TOKEN_ADDRESS"

# --- Dispatch -> Executor execution dispatch ---------------------------------
class DispatchNotFoundError(NotFoundError):
    code = "DISPATCH_NOT_FOUND"

class DispatchNotClaimableError(AppError):
    status_code = 409
    code = "DISPATCH_NOT_CLAIMABLE"

class DispatchResultConflictError(AppError):
    """Same dispatch_id returned a DIFFERENT immutable terminal result."""
    status_code = 409
    code = "DISPATCH_RESULT_CONFLICT"

class DispatchStateError(AppError):
    """Illegal dispatch state transition (fail closed)."""
    status_code = 409
    code = "DISPATCH_ILLEGAL_TRANSITION"

# --- Settlement cross-module control (STOP/RESUME receiver) ----------------------
class SettlementStopResumeConflictError(AppError):
    """Same command_id delivered with a different payload for STOP/RESUME."""
    status_code = 409
    code = "SETTLEMENT_STOP_RESUME_CONFLICT"

class SettlementStopResumeInvalidError(AppError):
    """Unsupported control contract version or wrong receiver module."""
    status_code = 422
    code = "SETTLEMENT_STOP_RESUME_INVALID"

# --- Authentication / users -------------------------------------------------
class AuthenticationError(AppError):
    status_code = 401
    code = "AUTHENTICATION_REQUIRED"

class InvalidCredentialsError(AppError):
    status_code = 401
    code = "INVALID_CREDENTIALS"

class PermissionDeniedError(AppError):
    status_code = 403
    code = "PERMISSION_DENIED"

class UserNotFoundError(NotFoundError):
    code = "USER_NOT_FOUND"

class DuplicateUserError(DuplicateError):
    code = "DUPLICATE_USER"
