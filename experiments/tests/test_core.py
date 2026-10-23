from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest
from app.providers.settlement_execution.dex_pancake import quote_v2_exact_in

from experiments.agents.arbitrageur import rebalance
from experiments.analysis.report import auc, cluster_interval
from experiments.costs import evasion
from experiments.data.history import load_history, write_frame
from experiments.data.synthetic import generate
from experiments.entities import entity_map, funding_edges
from experiments.indicators import (
    apply_thresholds,
    burst_revert,
    calibrate,
    net_position_ratio,
    reversal_share,
)
from experiments.protocol import verify_vendor
from experiments.sandbox.cpmm import CpmmPool


def fill(pool, side, amount, ts=1, wallet="w0"):
    return pool.swap_exact_in(
        side=side,
        amount_in_raw=int(amount),
        ts=ts,
        actor="manip",
        wallet=wallet,
        quote_usd=1,
    )


def test_thousand_integer_quotes():
    rng = np.random.default_rng(42)
    for _ in range(1000):
        q, t, amount = [int(v) * 10**18 for v in rng.integers(1, 10**9, 3)]
        pool = CpmmPool(q, t)
        side = "BUY" if amount % 3 else "SELL"
        expected = (
            quote_v2_exact_in(amount, q, t)
            if side == "BUY"
            else quote_v2_exact_in(amount, t, q)
        )
        trade = fill(pool, side, amount)
        assert trade.amount_out_raw == expected
        assert pool.reserve_quote_raw * pool.reserve_token_raw >= q * t


@pytest.mark.parametrize("rho,lower,upper", [(0.001, 24.9, 25.1), (0.01, 24.6, 24.9)])
def test_proposition_one(rho, lower, upper):
    costs = []
    for wallet in ["w0", "w1"]:
        pool = CpmmPool(10**24, 10**24)
        a = fill(pool, "BUY", int(rho * 10**24))
        b = fill(pool, "SELL", a.amount_out_raw, wallet=wallet)
        recovered = b.amount_out_raw / a.amount_in_raw
        predicted = 0.9975**2 * (1 + rho) / (1 + rho * 0.9975**2)
        assert recovered == pytest.approx(predicted, rel=1e-12)
        # Proposition 1 uses quote-leg volume; empirical cost uses input notional.
        cost = (
            (a.amount_in_raw - b.amount_out_raw)
            / (a.amount_in_raw + b.amount_out_raw)
            * 10000
        )
        assert lower <= cost <= upper
        costs.append(cost)
    assert costs[0] == costs[1]


def test_replay_fidelity_and_lossless_parquet(tmp_path):
    generate(tmp_path, 1, hours=0.1)
    meta, swaps, blocks = load_history(tmp_path)
    assert int(meta["reserve_quote_raw"]) > 2**63
    assert swaps.amount_in_raw.iloc[0] > 2**63
    swaps.loc[0, "amount_out_raw"] += 100
    write_frame(swaps, tmp_path / "swaps.parquet")
    with pytest.raises(ValueError, match="Nonstandard"):
        load_history(tmp_path)


def test_proposition_three_share_bound():
    rng = np.random.default_rng(42)
    for v, organic, tau in zip(
        rng.uniform(0, 1e8, 1000),
        rng.uniform(1, 1e8, 1000),
        rng.uniform(0.001, 0.999, 1000),
        strict=False,
    ):
        if v / (v + organic) <= tau:
            assert v <= tau / (1 - tau) * organic + 1e-8


def test_arbitrage_reaches_reference():
    for target in [0.25, 4.0]:
        pool = CpmmPool(10**24, 10**24)
        trade = rebalance(pool, target, 1, 1)
        assert trade.actor == "arb"
        assert pool.price() == pytest.approx(target, rel=1e-12)
    assert rebalance(pool, pool.price(), 2, 1) is None


def test_funding_and_views():
    edges = funding_edges(
        ["w0", "w1"], True, 3, dict(min_amount=5, max_amount=15, fixed_amount=10)
    )
    assert edges == funding_edges(
        ["w0", "w1"], True, 3, dict(min_amount=5, max_amount=15, fixed_amount=10)
    )
    for view in ["V0", "V1"]:
        mapping = entity_map(["w0", "w1", "org"], ["w0", "w1"], edges, view)
        assert mapping["w0"] == mapping["w1"] != mapping["org"]
    mapping = entity_map(["w0", "w1"], ["w0", "w1"], edges, "V0", 0)
    assert mapping["w0"] != mapping["w1"]


def test_fifo_partial_matching_and_wallet_attribution():
    data = pd.DataFrame(
        [
            dict(ts=0, side="BUY", notional_usd=100, wallet="w0"),
            dict(ts=10, side="SELL", notional_usd=40, wallet="w1"),
            dict(ts=20, side="SELL", notional_usd=60, wallet="w1"),
        ]
    )
    assert reversal_share(data, 30) == 1
    assert reversal_share(data, 30, True) == 0
    assert reversal_share(data, 5) == 0
    assert reversal_share(data.iloc[:2], 30) == pytest.approx(80 / 140)


def test_net_position_and_burst_censoring():
    p = CpmmPool(10**24, 10**24)
    a = fill(p, "BUY", 10**21, ts=10)
    b = fill(p, "SELL", a.amount_out_raw, ts=20)
    df = pd.DataFrame([asdict(a), asdict(b)])
    df["sequence"] = [0, 1]
    assert net_position_ratio(df) == pytest.approx(1)
    burst, rev = burst_revert(df, df, 30, 25)
    assert np.isnan(burst) and np.isnan(rev)
    _, rev = burst_revert(df, df, 30, 100)
    assert 0 < rev < 1


def test_integer_and_side_guards():
    pool = CpmmPool(1000, 1000)
    assert fill(pool, "BUY", 0) is None
    with pytest.raises(ValueError):
        fill(pool, "INVALID", 10)
    with pytest.raises(TypeError):
        pool.swap_exact_in(
            side="BUY", amount_in_raw=1.2, ts=1, actor="manip", wallet="w0"
        )


def test_vendor_integrity():
    manifest = verify_vendor()
    assert set(manifest) == {"files", "git_blobs"}
    assert manifest["files"] and manifest["git_blobs"]


def test_pool_cluster_bootstrap_and_auc():
    low, high = cluster_interval([1, 1, 1], 100, 2)
    assert low == high == 1
    assert all(np.isnan(cluster_interval([1], 100, 2)))
    assert auc([False, True], [0, 1]) == 1
    assert auc([False, True], [1, 1]) == 0.5


def test_calibration_only_and_strict_ties():
    frame = pd.DataFrame(
        [
            dict(
                pool="cal",
                entity="org",
                actor="organic",
                view="V1",
                indicator="a",
                value=x,
                aux_value=np.nan,
                eligible=True,
            )
            for x in [0, 0, 0, 0]
        ]
    )
    thresholds = calibrate(frame, [95, 99])
    frame.loc[0, "value"] = 1
    frame.loc[0, "actor"] = "manip"
    result = apply_thresholds(frame, thresholds)
    assert result.loc[result.actor == "manip", "fired"].all()
    assert not result.loc[result.actor == "organic", "fired"].any()


def test_evasion_target_conditioned_baseline():
    from experiments.indicators import INDICATORS

    rows = []
    for volume, cost, rate in [(1, -999, 0), (100, 10, 1), (100, 13, 0)]:
        r = dict(
            pool="p",
            view="V1",
            threshold_pct=95,
            robust=False,
            scenario="primary",
            **{"lambda": 1},
            V=volume,
            c_bps=cost,
        )
        for k in INDICATORS:
            r["p_" + k] = rate
            r["coverage_" + k] = 1
        rows.append(r)
    result = evasion(pd.DataFrame(rows), dict(V_star_usd=50, epsilon=0.05))
    assert (result.delta_c_bps == 3).all()
    result = evasion(pd.DataFrame(rows[:2]), dict(V_star_usd=50, epsilon=0.05))
    assert np.isinf(result.delta_c_bps).all()
    result = evasion(pd.DataFrame(rows[:1]), dict(V_star_usd=50, epsilon=0.05))
    assert result.delta_c_bps.isna().all()
