"""Historical exact-input replay, preserving block/log order."""


def replay(pool, row):
    """Replay one historical organic swap as an exact-input fill.

    Args:
        pool: :class:`CpmmPool` to trade against.
        row: One historical swap record.

    Returns:
        A :class:`SwapFill`, or "None" if the swap is unfillable.
    """
    return pool.swap_exact_in(
        side=row.side,
        amount_in_raw=int(row.amount_in_raw),
        ts=int(row.ts),
        actor="organic",
        wallet=row.trader,
        quote_usd=float(row.quote_usd),
    )
