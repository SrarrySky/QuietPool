"""DEX transaction-building boundary (V4.1B).

Separate concern: the DEX builder returns UNSIGNED transaction material only —
never signed bytes, never private keys. Live quote/build for Jupiter and
PancakeSwap live in ``dex_jupiter.py`` / ``dex_pancake.py``. The default
``UnavailableDexBuilder`` fails closed (``DEX_NOT_CONFIGURED``) and never
fabricates quotes, liquidity, price impact, min_out or transaction bytes.

PAPER fixtures remain PAPER and are handled by the paper provider — they are
never presented as live evidence.

Liquidity terminology (do not relabel):
- ``quote_price``      = input/output relationship from an ACTUAL quote
- ``target_price``     = strategy/request target (sizing layer)
- ``price_impact``     = only when a provider can genuinely derive it
- ``pool_liquidity``   = provider/on-chain evidence, with explicit units
- ``liquidity_depth``  = returned as a number ONLY when a defined
  calculation/source exists; otherwise ``null`` + ``QUOTE_ONLY`` status.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.providers.settlement_execution.errors import SettlementDexNotConfiguredError


@dataclass(frozen=True)
class RouteLegEvidence:
    """Per-leg route evidence (provider-faithful; e.g. Jupiter routePlan)."""

    amm_key: str | None = None
    label: str | None = None
    input_mint: str | None = None
    output_mint: str | None = None
    in_amount_raw: int | None = None
    out_amount_raw: int | None = None
    percent_bps: int | None = None


@dataclass(frozen=True)
class LiquidityEvidence:
    """Honest liquidity evidence with explicit units.

    ``kind``: PAIR_RESERVES | V3_POOL | QUOTE_ONLY | NONE. ``liquidity_depth``
    must NOT be invented; a QUOTE_ONLY result carries no authoritative depth.
    """

    kind: str = "NONE"
    units: str = ""
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QuoteEvidence:
    """Normalized exact-in quote evidence (never a guarantee).

    ``expected_amount_out_raw`` is the provider's QUOTED expected output, not a
    guarantee. ``minimum_amount_out_raw`` is the slippage-protected floor.
    """

    chain: str
    venue: str
    input_mint: str
    output_mint: str
    amount_in_raw: int
    expected_amount_out_raw: int
    minimum_amount_out_raw: int
    slippage_bps: int
    quote_price: Decimal | None = None
    route_legs: tuple[RouteLegEvidence, ...] = ()
    liquidity: LiquidityEvidence | None = None
    validity: dict[str, Any] = field(default_factory=dict)
    source_fields: dict[str, Any] = field(default_factory=dict)

    @property
    def effective_quote_price(self) -> Decimal | None:
        """input/output relationship from the actual quote (Decimal, never float)."""
        if self.expected_amount_out_raw is None or self.amount_in_raw == 0:
            return None
        return Decimal(self.expected_amount_out_raw) / Decimal(self.amount_in_raw)


@dataclass(frozen=True)
class ApprovalRequirement:
    """ERC-20 / Permit2 authorization model. NEVER includes a signature.

    ``status``: NATIVE_INPUT (no approval needed) | SUFFICIENT | INSUFFICIENT |
    UNKNOWN (allowance could not be proven -> do not pretend executable).
    No fake Permit2 signature is ever generated.
    """

    status: str
    token: str | None = None
    spender: str | None = None
    required_allowance_raw: int | None = None
    current_allowance_raw: int | None = None


@dataclass(frozen=True)
class UnsignedEvmTransaction:
    """UNSIGNED EVM transaction payload. Never contains private keys/signatures."""

    chain_id: int
    from_address: str
    to_address: str
    data: str
    value: int
    deadline: int
    gas: int | None = None
    nonce: int | None = None
    gas_price: int | None = None  # wei; explicit (engine-supplied), never inferred
    validity: dict[str, Any] = field(default_factory=dict)
    unsigned: bool = True


@dataclass(frozen=True)
class DexBuildRequest:
    """CANONICAL DEX build input (V4.2D remediation).

    The execution engine/planning layer owns every field explicitly. A builder
    must NEVER infer token pairs, taker, amount or slippage from unrelated order
    fields. Absent fields are passed through as ``None``; the REAL builders fail
    closed with ``MISSING_REQUIRED_INPUT`` instead of guessing.

    ``to_kwargs()`` emits the canonical keyword surface the builders already
    accept (``input_mint`` / ``output_mint`` / ``taker_public_key`` / ``taker`` /
    ``from_address`` / ``amount_raw`` / ``slippage_bps`` / ``deadline`` /
    ``nonce`` / ``gas`` / ``gas_price`` / ``recipient`` / ``max_accounts``).
    """

    chain: str
    venue: str | None = None
    wallet_address: str | None = None  # taker / from
    input_mint: str | None = None  # token sold (BUY: quote asset, SELL: the token)
    output_mint: str | None = None  # token bought
    amount_raw: int | None = None  # exact input amount in raw units
    slippage_bps: int | None = None  # explicit; 0 is valid (never truthiness-defaulted)
    deadline: int | None = None  # EVM only
    recipient: str | None = None  # EVM recipient (defaults to taker)
    nonce: int | None = None  # EVM only
    gas: int | None = None  # EVM only
    gas_price: int | None = None  # EVM only (wei)
    execution_kind: str | None = None  # BUNDLE / NORMAL
    provider_target: str | None = None  # JITO / 48CLUB
    max_accounts: int | None = None  # Solana ALT budget

    def to_kwargs(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "venue": self.venue,
            "taker_public_key": self.wallet_address,
            "taker": self.wallet_address,
            "from_address": self.wallet_address,
            "input_mint": self.input_mint,
            "output_mint": self.output_mint,
            "token_in": self.input_mint,
            "token_out": self.output_mint,
            "amount_raw": self.amount_raw,
            "slippage_bps": self.slippage_bps,
            "deadline": self.deadline,
            "recipient": self.recipient,
            "nonce": self.nonce,
            "gas": self.gas,
            "gas_price": self.gas_price,
            "execution_kind": self.execution_kind,
            "provider_target": self.provider_target,
            "max_accounts": self.max_accounts,
        }


@dataclass(frozen=True)
class DexBuildResult:
    """UNSIGNED DEX build output.

    ``unsigned`` holds the object (solders VersionedTransaction / UnsignedEvmTransaction);
    ``unsigned_serialized`` is the transport form (base64 / 0x-hex). Both are
    explicitly UNSIGNED and must not reach a bundle provider until the external
    signer returns signed transactions. ``status``: AVAILABLE | PARTIAL |
    UNAVAILABLE.
    """

    chain: str
    venue: str
    unsigned: Any
    unsigned_serialized: str
    quote: QuoteEvidence
    approval: ApprovalRequirement | None = None
    status: str = "AVAILABLE"
    warnings: tuple[str, ...] = ()


class DexTransactionBuilder(ABC):
    """Builds UNSIGNED swap transaction / message material (never signed bytes)."""

    name: str = "abstract"

    @abstractmethod
    def is_configured(self) -> bool:
        """True when a live DEX quote/transaction source is configured."""

    @abstractmethod
    def build_solana_swap(self, **kwargs: Any) -> DexBuildResult:
        """Return an UNSIGNED Solana (Jupiter) swap build result."""

    @abstractmethod
    def build_bnb_swap(self, **kwargs: Any) -> DexBuildResult:
        """Return an UNSIGNED BNB (PancakeSwap V2/V3) swap build result."""


class UnavailableDexBuilder(DexTransactionBuilder):
    name = "UNAVAILABLE_DEX"

    def is_configured(self) -> bool:
        return False

    def build_solana_swap(self, **kwargs: Any) -> DexBuildResult:
        raise SettlementDexNotConfiguredError(
            "Jupiter live swap construction is not configured",
            details={"builder": self.name},
        )

    def build_bnb_swap(self, **kwargs: Any) -> DexBuildResult:
        raise SettlementDexNotConfiguredError(
            "PancakeSwap live swap construction is not configured",
            details={"builder": self.name},
        )
