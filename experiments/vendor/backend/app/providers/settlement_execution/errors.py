"""Controlled Settlement execution-provider errors.
Raw provider exceptions / response bodies / URLs / credentials are NEVER exposed
through the Settlement API. Every error carries a stable non-sensitive ``code``;
detailed diagnostics stay internal.
"""
from typing import Any
from app.core.errors import AppError

class SettlementExecutionProviderError(AppError):
    """Base for Settlement execution-provider domain errors."""
    status_code = 502  # upstream provider failure
    code = "PROVIDER_ERROR"

class SettlementProviderNotConfiguredError(SettlementExecutionProviderError):
    code = "PROVIDER_NOT_CONFIGURED"

class SettlementSignerNotConfiguredError(SettlementExecutionProviderError):
    status_code = 403
    code = "SIGNER_NOT_CONFIGURED"

class SettlementDexNotConfiguredError(SettlementExecutionProviderError):
    status_code = 403
    code = "DEX_NOT_CONFIGURED"

class SettlementSubmissionValidationError(SettlementExecutionProviderError):
    status_code = 422
    code = "SUBMISSION_INVALID"

class SettlementProviderRejectedError(SettlementExecutionProviderError):
    """Provider definitively rejected the submission (not ambiguous)."""
    status_code = 502
    code = "BUNDLE_REJECTED"

class SettlementProviderUnavailableError(SettlementExecutionProviderError):
    status_code = 503
    code = "PROVIDER_UNAVAILABLE"

class SettlementProviderRequestError(SettlementExecutionProviderError):
    """Ambiguous transport failure: the submission outcome cannot be proven."""
    status_code = 502
    code = "PROVIDER_REQUEST_FAILED"

class SettlementProviderNotFoundError(SettlementProviderRequestError):
    """Provider has no record of the reference (e.g. 404 on status lookup)."""
    code = "PROVIDER_NOT_FOUND"

class SettlementDexQuoteUnavailableError(SettlementExecutionProviderError):
    """A DEX quote could not be obtained (no trustworthy evidence)."""
    status_code = 503
    code = "DEX_QUOTE_UNAVAILABLE"

class SettlementDexRouteUnavailableError(SettlementExecutionProviderError):
    """No valid route exists for the requested pair/venue."""
    code = "DEX_ROUTE_UNAVAILABLE"

class SettlementDexBuildFailedError(SettlementExecutionProviderError):
    """Unsigned transaction construction failed."""
    code = "DEX_BUILD_FAILED"

class SettlementDexInvalidResponseError(SettlementExecutionProviderError):
    """The DEX provider returned a malformed/unexpected response."""
    code = "DEX_INVALID_RESPONSE"

class SettlementDexSlippageInvalidError(SettlementExecutionProviderError):
    """Slippage value is out of the valid [0, 10000) range."""
    status_code = 422
    code = "DEX_SLIPPAGE_INVALID"

class SettlementDexTransactionStaleError(SettlementExecutionProviderError):
    """The unsigned build is stale (blockhash/lastValidBlockHeight expired)."""
    code = "DEX_TRANSACTION_STALE"

class SettlementDexBlockhashInvalidError(SettlementExecutionProviderError):
    """The build's blockhash is not live-valid per the Solana RPC
    (``isBlockhashValid`` false) or its validity is unknown/unreadable."""
    code = "DEX_BLOCKHASH_INVALID"

class SettlementExecutionNetworkMismatchError(SettlementExecutionProviderError):
    """A signed payload's network/cluster does not match the target provider
    endpoint (e.g. testnet-signed material against a mainnet provider). Fail
    closed BEFORE any provider transport I/O."""
    code = "EXECUTION_NETWORK_MISMATCH"

class SettlementUnsupportedCapabilityError(SettlementExecutionProviderError):
    """A requested safety/execution parameter is NOT supported by the current
    reviewed provider contract and would otherwise be silently accepted-and-
    ignored (V4.2F-remediation, gas/tip honesty). Fail closed instead."""
    status_code = 422
    code = "UNSUPPORTED_CAPABILITY"

class SettlementDexApprovalRequiredError(SettlementExecutionProviderError):
    """Swap execution requires an ERC-20 approval / Permit2 authorization."""
    status_code = 403
    code = "DEX_APPROVAL_REQUIRED"

class SettlementLiveReadNotConfiguredError(SettlementExecutionProviderError):
    """Live read-only probe requested but the required RPC/API is not configured."""
    status_code = 503
    code = "LIVE_READ_NOT_CONFIGURED"

class SettlementChainIdMismatchError(SettlementExecutionProviderError):
    """The RPC chain id does not match the configured expected chain."""
    code = "CHAIN_ID_MISMATCH"

class SettlementContractNotDeployedError(SettlementExecutionProviderError):
    """An expected on-chain contract has no code (eth_getCode empty)."""
    code = "CONTRACT_NOT_DEPLOYED"

class SettlementSignerCapabilityGapError(SettlementExecutionProviderError):
    """The configured external signer/custody path is incompatible with the
    Jito/48 Club bundle signer boundary."""
    status_code = 403
    code = "SIGNER_CAPABILITY_GAP"

class SettlementSignerResponseInvalidError(SettlementExecutionProviderError):
    """A signer returned a signature/signed transaction that does not match the
    approved intent (mismatched fields / invalid signature / wrong sender)."""
    status_code = 403
    code = "SIGNER_RESPONSE_INVALID"

class SettlementSignerStatusUnknownError(SettlementExecutionProviderError):
    """The external signer's status/outcome could not be determined."""
    code = "SIGNER_STATUS_UNKNOWN"

class SettlementSignerTransactionTypeUnsupportedError(SettlementExecutionProviderError):
    """The EVM transaction type is not supported (only legacy EIP-155 is
    implemented); a type-2/1559 request must never be hashed as legacy."""
    status_code = 422
    code = "SIGNER_TRANSACTION_TYPE_UNSUPPORTED"

class SettlementSignerUnavailableError(SettlementExecutionProviderError):
    """The external signing service is unreachable / unavailable (V4.2C.1)."""
    status_code = 503
    code = "SIGNER_UNAVAILABLE"

class SettlementSignerAuthenticationError(SettlementExecutionProviderError):
    """External signing service rejected the credential/authentication (V4.2C.1)."""
    status_code = 401
    code = "SIGNER_AUTH_FAILED"

class SettlementSignerAuthorizationRejectedError(SettlementExecutionProviderError):
    """External signing service authorization was rejected (V4.2C.1)."""
    status_code = 403
    code = "SIGNER_AUTHORIZATION_REJECTED"

class SettlementSignerTimeoutError(SettlementExecutionProviderError):
    """The external signing service did not answer in time (V4.2C.1)."""
    status_code = 504
    code = "SIGNER_TIMEOUT"

class SettlementSignerRateLimitedError(SettlementExecutionProviderError):
    """External signing service rate limited / temporary provider error (V4.2C.1)."""
    status_code = 429
    code = "SIGNER_RATE_LIMITED"

# Codes whose final outcome is AMBIGUOUS: never fabricate success or failure.
AMBIGUOUS_PROVIDER_CODES = {
    "PROVIDER_REQUEST_FAILED",
    "PROVIDER_UNAVAILABLE",
    "PROVIDER_NOT_FOUND",
    "PROVIDER_TIMEOUT",
    "SIGNER_UNAVAILABLE",
    "SIGNER_TIMEOUT",
}
# Codes that are a definitive provider rejection.
DEFINITIVE_REJECT_CODES = {
    "BUNDLE_REJECTED",
    "PROVIDER_RATE_LIMITED",
}

def sanitize_provider_error(exc: BaseException) -> tuple[str, str]:
    """Return (code, sanitized_message) with NO URL / headers / body / secrets.
    Unexpected exceptions collapse to a controlled token so that API responses,
    logs and the persisted audit record never carry raw provider text.
    """
    if isinstance(exc, SettlementExecutionProviderError):
        return exc.code, exc.message
    return "PROVIDER_REQUEST_FAILED", "provider request failed"
