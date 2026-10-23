"""Deterministic synthetic fixtures for software validation, not historical evidence.

These fixtures exercise the replay machinery and are never presented as
historical observations.
"""

import math

import pandas as pd

from ..sandbox.cpmm import CpmmPool
from .history import save_history


def generate(folder, index, hours=6):
    """Write a deterministic synthetic history for one pool.

    Generates pseudo-random organic swaps against a constant-product pool on a
    30-second grid, then persists them with :func:`save_history`.

    Args:
        folder: Output directory for the history artifacts.
        index: Pool index used to vary the synthetic activity pattern.
        hours: Length of the synthetic interval in hours.
    """
    pool = CpmmPool(10**24, 10**24)
    initial = dict(
        pair=f"synthetic_{index}",
        source="synthetic",
        quote_decimals=18,
        token_decimals=18,
        reserve_quote_raw=str(pool.reserve_quote_raw),
        reserve_token_raw=str(pool.reserve_token_raw),
    )
    rows = []
    blocks = []
    start = 1700000000
    for n in range(int(hours * 3600 / 30) + 1):
        ts = start + n * 30
        blocks.append(dict(block=n + 1, ts=ts, quote_usd=1.0))
        if n % 11 == 0:
            continue
        side = (
            "BUY" if math.sin(n / (5 + index)) + 0.4 * math.cos(n / 19) > 0 else "SELL"
        )
        amount = int((300 + index * 150 + 180 * (1 + math.sin(n * 0.31))) * 10**18)
        fill = pool.swap_exact_in(
            side=side,
            amount_in_raw=amount,
            ts=ts,
            actor="organic",
            wallet=f"organic_{n % 13}",
            quote_usd=1.0,
        )
        rows.append(
            dict(
                block=n + 1,
                ts=ts,
                tx_hash=f"fixture_{n}",
                log_index=1,
                trader=fill.wallet,
                side=side,
                amount_in_raw=amount,
                amount_out_raw=fill.amount_out_raw,
                reserve_quote_after_raw=pool.reserve_quote_raw,
                reserve_token_after_raw=pool.reserve_token_raw,
                quote_usd=1.0,
            )
        )
    save_history(folder, initial, pd.DataFrame(rows), pd.DataFrame(blocks))
