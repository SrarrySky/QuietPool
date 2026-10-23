"""Block-ordered replay with physical balances and independently valued costs."""

from collections import deque
from dataclasses import asdict

import numpy as np
import pandas as pd

from .agents.arbitrageur import rebalance
from .agents.manipulator import Manipulator
from .agents.organic import replay
from .data.liquidity import apply_liquidity, ordered_events
from .entities import funding_edges
from .sandbox.cpmm import CpmmPool, SwapFill


def simulate(meta, swaps, blocks, cfg, ladder, spec=None, seed=42, budget=None):
    """Replay one pool's history with physical balances and independent costs.

    Blocks are processed in order, applying historical liquidity events and
    organic swaps, then the manipulator (when "spec" is supplied) and the
    arbitrageur. Marked-wealth snapshots are recorded per block so that costs
    can be valued independently of the fill stream.

    Args:
        meta: Pool metadata mapping (reserves, decimals, quote price).
        swaps: DataFrame of historical swaps.
        blocks: DataFrame of per-block quote prices and timestamps.
        cfg: Frozen configuration mapping.
        ladder: Strategy ladder settings.
        spec: Optional manipulator configuration dict; "None" disables it.
        seed: Funding/random seed for the manipulator (default 42).
        budget: Optional :class:`Budget` for cooperative checkpointing.

    Returns:
        Tuple "(trades, marks, edges, decisions)".

    Raises:
        ValueError: If history is shorter than the requested horizon.
    """
    pool = CpmmPool(
        int(meta["reserve_quote_raw"]),
        int(meta["reserve_token_raw"]),
        meta["quote_decimals"],
        meta["token_decimals"],
        cfg["fee_bps"],
    )
    start = int(blocks.ts.iloc[0])
    end = start + int(cfg["horizon_hours"] * 3600)
    if blocks.ts.iloc[-1] < end:
        raise ValueError("History is shorter than the requested horizon")
    q0 = float(blocks.quote_usd.iloc[0])
    balances = [
        cfg["initial_quote_usd"] / q0,
        cfg["initial_token_usd"] / (pool.price() * q0),
    ]
    initial_token = balances[1]
    initial_quote = balances[0]
    trader = Manipulator(spec, ladder["wallets"]) if spec else None
    edges = (
        funding_edges(trader.wallets, spec["level"] >= 4, seed, ladder["funding"])
        if trader
        else []
    )
    fills = []
    prices = deque([(start, pool.price())])
    organic = deque()
    marks = [
        dict(
            ts=start,
            equity=(balances[0] + balances[1] * pool.price()) * q0,
            inventory_usd=balances[1] * pool.price() * q0,
            inventory_change_usd=0.0,
            passive_equity=(initial_quote + initial_token * pool.price()) * q0,
            incremental_equity=0.0,
            token_balance=balances[1],
            quote_balance=balances[0],
        )
    ]
    grouped = {}
    for n, _, is_liquidity, row in ordered_events(meta, swaps):
        grouped.setdefault(n, []).append((is_liquidity, row))
    reference = pool.price()
    last_quote = q0
    for block_index, block in enumerate(blocks.itertuples()):
        if budget and block_index % 128 == 0:
            budget.check()
        ts = int(block.ts)
        if ts >= end:
            break
        last_quote = float(block.quote_usd)
        if block.block in grouped:
            for is_liquidity, row in grouped[block.block]:
                if is_liquidity:
                    apply_liquidity(pool, row)
                else:
                    fill = replay(pool, row)
                    if fill:
                        fills.append(fill)
                        organic.append((ts, fill.notional_usd))
                reference = (
                    int(row.reserve_quote_after_raw)
                    / int(row.reserve_token_after_raw)
                    * 10 ** (pool.token_decimals - pool.quote_decimals)
                )
        while organic and organic[0][0] <= ts - 60:
            organic.popleft()
        while prices and prices[0][0] < ts - 1860:
            prices.popleft()
        if trader:
            fills.extend(
                trader.act(
                    pool, ts, last_quote, sum(v for _, v in organic), prices, balances
                )
            )
        if cfg["arbitrageur"]["enabled"]:
            fill = rebalance(
                pool, reference, ts, last_quote, cfg["arbitrageur"]["band_bps"]
            )
            if fill:
                fills.append(fill)
        prices.append((ts, pool.price()))
        marks.append(
            dict(
                ts=ts,
                equity=(balances[0] + balances[1] * pool.price()) * last_quote,
                inventory_usd=balances[1] * pool.price() * last_quote,
                inventory_change_usd=(balances[1] - initial_token)
                * pool.price()
                * last_quote,
                passive_equity=(initial_quote + initial_token * pool.price())
                * last_quote,
                incremental_equity=(
                    (balances[0] - initial_quote)
                    + (balances[1] - initial_token) * pool.price()
                )
                * last_quote,
                token_balance=balances[1],
                quote_balance=balances[0],
            )
        )
    marks.append(dict(marks[-1], ts=end))
    trades = pd.DataFrame(
        [asdict(f) for f in fills], columns=SwapFill.__dataclass_fields__
    )
    trades["sequence"] = np.arange(len(trades))
    return trades, pd.DataFrame(marks), edges, trader.decisions if trader else []


def realized_volatility(swaps, liquidity=()):
    """Estimate per-sqrt-second realized volatility from one-minute prices.

    Uses the last observed reserve-ratio price per minute and forward-fills
    gaps, matching the paper's baseline-volatility definition.

    Args:
        swaps: DataFrame with reserve and timestamp columns.
        liquidity: Optional list of liquidity-event records to include.

    Returns:
        Sample standard deviation of log returns divided by "sqrt(60)", or
        "NaN" when fewer than two observations are available.
    """
    if liquidity:
        columns = [
            "block",
            "log_index",
            "ts",
            "reserve_quote_after_raw",
            "reserve_token_after_raw",
        ]
        swaps = pd.concat(
            [swaps[columns], pd.DataFrame(liquidity)[columns]], ignore_index=True
        ).sort_values(["block", "log_index"])
    prices = swaps.reserve_quote_after_raw.map(
        float
    ) / swaps.reserve_token_after_raw.map(float)
    minute = (
        pd.Series(prices.to_numpy(), index=pd.to_datetime(swaps.ts, unit="s"))
        .resample("1min")
        .last()
        .ffill()
    )
    returns = np.diff(np.log(minute.to_numpy()))
    # Sigma per sqrt(second), matching Proposition 2's tau in seconds.
    return float(np.std(returns, ddof=1) / np.sqrt(60)) if len(returns) > 1 else np.nan
