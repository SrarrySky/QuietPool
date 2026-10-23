"""Liquidity changes affect reserves, not manufactured-volume trade counts."""

from types import SimpleNamespace

import pandas as pd
import pytest

from experiments.data.history import validate_history
from experiments.data.liquidity import MODEL, apply_liquidity
from experiments.engine import realized_volatility, simulate
from experiments.sandbox.cpmm import CpmmPool


def fixture():
    meta = dict(
        reserve_quote_raw="1000000",
        reserve_token_raw="1000000",
        quote_decimals=0,
        token_decimals=0,
        liquidity_model=MODEL,
    )
    pool = CpmmPool(1000000, 1000000, 0, 0)
    swaps, events = [], []

    def trade(block):
        fill = pool.swap_exact_in(
            side="BUY",
            amount_in_raw=10000,
            ts=block - 10,
            actor="organic",
            wallet="origin",
        )
        swaps.append(
            dict(
                block=block,
                ts=block - 10,
                log_index=2,
                tx_hash=str(block),
                trader="origin",
                side="BUY",
                amount_in_raw=10000,
                amount_out_raw=fill.amount_out_raw,
                reserve_quote_after_raw=pool.reserve_quote_raw,
                reserve_token_after_raw=pool.reserve_token_raw,
                quote_usd=1.0,
            )
        )

    def change(block, index, kind, q, t):
        event = dict(
            block=block,
            ts=block - 10,
            log_index=index,
            tx_hash=str(block),
            trader="origin",
            kind=kind,
            amount_quote_raw=str(q),
            amount_token_raw=str(t),
        )
        apply_liquidity(pool, SimpleNamespace(**event))
        event.update(
            reserve_quote_after_raw=str(pool.reserve_quote_raw),
            reserve_token_after_raw=str(pool.reserve_token_raw),
        )
        events.append(event)

    trade(10)
    change(10, 4, "MINT", 100000, 200000)
    change(11, 1, "BURN", 50000, 100000)
    trade(11)
    meta["liquidity_events"] = events
    blocks = pd.DataFrame(dict(block=[10, 11, 12], ts=[0, 1, 2], quote_usd=[1.0] * 3))
    return meta, pd.DataFrame(swaps), blocks


def test_exact_historical_accounting_and_timestamp_guard():
    meta, swaps, blocks = fixture()
    assert validate_history(meta, swaps, blocks)
    meta["liquidity_events"][0]["reserve_quote_after_raw"] = "1"
    with pytest.raises(ValueError, match="Liquidity Sync"):
        validate_history(meta, swaps, blocks)
    meta, swaps, blocks = fixture()
    meta["liquidity_events"][0]["ts"] = 99
    with pytest.raises(ValueError, match="timestamp"):
        validate_history(meta, swaps, blocks)


@pytest.mark.parametrize("scale", [1, 2])
def test_engine_preserves_order_and_counterfactual_reserves(scale):
    meta, swaps, blocks = fixture()
    meta["reserve_quote_raw"] = meta["reserve_token_raw"] = str(1000000 * scale)
    cfg = dict(
        fee_bps=25,
        horizon_hours=2 / 3600,
        initial_quote_usd=100,
        initial_token_usd=20,
        arbitrageur=dict(enabled=False),
    )
    trades, marks, _, _ = simulate(meta, swaps, blocks, cfg, {})
    manual = CpmmPool(1000000 * scale, 1000000 * scale, 0, 0)

    def trade(ts):
        return manual.swap_exact_in(
            side="BUY", amount_in_raw=10000, ts=ts, actor="organic", wallet="origin"
        )

    first = trade(0)
    for e in meta["liquidity_events"]:
        apply_liquidity(manual, SimpleNamespace(**e))
    second = trade(1)
    assert trades.amount_out_raw.tolist() == [
        first.amount_out_raw,
        second.amount_out_raw,
    ]
    assert len(trades) == 2
    assert marks.iloc[-1].equity == pytest.approx(100 + 20 * manual.price())


def test_infeasible_burn_fails_atomically():
    pool = CpmmPool(100, 100)
    event = SimpleNamespace(
        kind="BURN", amount_quote_raw="1", amount_token_raw="100", block=1, log_index=1
    )
    with pytest.raises(ValueError, match="Infeasible"):
        apply_liquidity(pool, event)
    assert (pool.reserve_quote_raw, pool.reserve_token_raw) == (100, 100)


def test_baseline_volatility_includes_liquidity_price_updates():
    first = dict(
        block=1,
        log_index=1,
        ts=0,
        reserve_quote_after_raw=100,
        reserve_token_after_raw=100,
    )
    events = [
        dict(
            first,
            block=2,
            ts=60,
            reserve_quote_after_raw="200",
            reserve_token_after_raw="100",
        ),
        dict(
            first,
            block=3,
            ts=120,
            reserve_quote_after_raw="300",
            reserve_token_after_raw="100",
        ),
    ]
    result = realized_volatility(pd.DataFrame([first]), events)
    assert result > 0
    assert result == pytest.approx(realized_volatility(pd.DataFrame([first] + events)))
