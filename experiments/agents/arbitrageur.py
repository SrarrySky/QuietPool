"""One block-end exact-in correction to the latest observed historical price."""

import math


def rebalance(pool, target, ts, quote_usd, band_bps=30):
    """Execute one exact-input swap to correct the pool toward a target price.

    Uses bisection on the input amount to find the smallest trade that brings
    the pool price within "band_bps" of "target".

    Args:
        pool: :class:`CpmmPool` to rebalance.
        target: Reference (historical) quote-per-token price.
        ts: Block timestamp in seconds.
        quote_usd: USD price of one quote unit.
        band_bps: Deviation tolerance in basis points.

    Returns:
        A :class:`SwapFill` if a correction is needed, otherwise "None".

    Raises:
        ValueError: If "target" is non-positive/non-finite or unbracketable.
    """
    if target <= 0 or not math.isfinite(target):
        raise ValueError("Invalid historical reference price")
    if abs(math.log(pool.price() / target)) * 10000 <= band_bps:
        return None
    side = "BUY" if pool.price() < target else "SELL"
    rq, rt = pool.reserve_quote_raw, pool.reserve_token_raw

    def resulting_price(amount):
        out = pool.quote(side, amount) or 0
        a, b = (rq + amount, rt - out) if side == "BUY" else (rq - out, rt + amount)
        return a / b * 10 ** (pool.token_decimals - pool.quote_decimals)

    lo, hi = 0, rq if side == "BUY" else rt

    def crossed(a):
        return (
            resulting_price(a) >= target
            if side == "BUY"
            else resulting_price(a) <= target
        )

    for _ in range(256):
        if crossed(hi):
            break
        hi *= 2
    else:
        raise ValueError("Arbitrage target cannot be bracketed")
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if crossed(mid):
            hi = mid
        else:
            lo = mid
    return pool.swap_exact_in(
        side=side,
        amount_in_raw=hi,
        ts=ts,
        actor="arb",
        wallet="arb",
        quote_usd=quote_usd,
    )
