"""Integer reserve accounting; the primary quote uses the PancakeSwap V2 code."""

import logging
import math
from dataclasses import dataclass

from app.providers.settlement_execution.dex_pancake import quote_v2_exact_in


@dataclass(frozen=True)
class SwapFill:
    """One executed swap fill with pre/post prices and USD valuations.

    "amount_in_raw"/"amount_out_raw" are in native token decimals, while
    "signed_token" and "quote_flow" are signed human-scale quantities used
    for balance bookkeeping.
    """

    ts: int
    actor: str
    wallet: str
    side: str
    amount_in_raw: int
    amount_out_raw: int
    fee_raw: int
    price_before: float
    price_after: float
    notional_usd: float
    fee_usd: float
    signed_token: float
    quote_flow: float
    quote_usd: float


@dataclass
class CpmmPool:
    """Constant-product market maker with integer raw-reserve accounting.

    Reserves are tracked as raw integers in their native decimals. Pricing for
    the primary 25 bps fee delegates to the PancakeSwap V2 code;
    other fees use an explicit counterfactual formula.
    """

    reserve_quote_raw: int
    reserve_token_raw: int
    quote_decimals: int = 18
    token_decimals: int = 18
    fee_bps: int = 25

    def __post_init__(self):
        if any(
            not isinstance(v, int) or isinstance(v, bool)
            for v in (self.reserve_quote_raw, self.reserve_token_raw)
        ):
            raise TypeError("Raw reserves must be Python integers")
        if min(self.reserve_quote_raw, self.reserve_token_raw) <= 0:
            raise ValueError("Reserves must be positive")
        if not (0 <= self.fee_bps < 10000):
            raise ValueError("Invalid fee")
        if not all(
            isinstance(d, int) and 0 <= d <= 36
            for d in (self.quote_decimals, self.token_decimals)
        ):
            raise ValueError("Invalid decimals")

    def price(self):
        """Return the quote-per-token price implied by current reserves."""
        return (self.reserve_quote_raw / self.reserve_token_raw) * 10 ** (
            self.token_decimals - self.quote_decimals
        )

    def quote(self, side, amount):
        """Return the raw output amount for an exact-input swap on "side".

        Args:
            side: "BUY" (quote in, token out) or "SELL" (token in,
                quote out).
            amount: Raw input amount in the input asset's native decimals.

        Returns:
            Raw output amount, or "None" if the input is not positive under
            the counterfactual fee formula.

        Raises:
            ValueError: If "side" is not "BUY" or "SELL".
        """
        if side not in {"BUY", "SELL"}:
            raise ValueError("Side must be BUY or SELL")
        ri, ro = (
            (self.reserve_quote_raw, self.reserve_token_raw)
            if side == "BUY"
            else (self.reserve_token_raw, self.reserve_quote_raw)
        )
        if self.fee_bps == 25:
            return quote_v2_exact_in(amount, ri, ro)
        # Explicit counterfactual fee sensitivity; never used for fidelity checks.
        if amount <= 0:
            return None
        net = amount * (10000 - self.fee_bps)
        return net * ro // (ri * 10000 + net)

    def swap_exact_in(self, *, side, amount_in_raw, ts, actor, wallet, quote_usd=1.0):
        """Apply an exact-input swap and return a :class:`SwapFill` if it fills.

        Args:
            side: "BUY" or "SELL".
            amount_in_raw: Raw input amount (positive integer).
            ts: Block timestamp in seconds.
            actor: Fill actor label ("manip", "organic", or "arb").
            wallet: Fill wallet identifier.
            quote_usd: USD price of one quote unit for valuation.

        Returns:
            :class:`SwapFill` describing the executed fill, or "None" if the
            input is unfillable.

        Raises:
            TypeError: If "amount_in_raw" is not an integer.
            ValueError: If "quote_usd" is not finite and positive.
        """
        if not isinstance(amount_in_raw, int) or isinstance(amount_in_raw, bool):
            raise TypeError("Raw input must be a Python integer")
        if not math.isfinite(quote_usd) or quote_usd <= 0:
            raise ValueError("Positive finite quote USD price required")
        out = self.quote(side, amount_in_raw)
        if out is None or out <= 0:
            logging.getLogger(__name__).debug(
                "Skipped unfillable %s input %s", side, amount_in_raw
            )
            return None
        before = self.price()
        fee = amount_in_raw * self.fee_bps // 10000
        unit = (
            quote_usd / 10**self.quote_decimals
            if side == "BUY"
            else before * quote_usd / 10**self.token_decimals
        )
        if side == "BUY":
            self.reserve_quote_raw += amount_in_raw
            self.reserve_token_raw -= out
            token, quote = (
                out / 10**self.token_decimals,
                -amount_in_raw / 10**self.quote_decimals,
            )
        else:
            self.reserve_token_raw += amount_in_raw
            self.reserve_quote_raw -= out
            token, quote = (
                -amount_in_raw / 10**self.token_decimals,
                out / 10**self.quote_decimals,
            )
        return SwapFill(
            ts,
            actor,
            wallet,
            side,
            amount_in_raw,
            out,
            fee,
            before,
            self.price(),
            amount_in_raw * unit,
            fee * unit,
            token,
            quote,
            quote_usd,
        )
