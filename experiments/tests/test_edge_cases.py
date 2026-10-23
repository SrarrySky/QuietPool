"""Third-review regressions: unavailable evidence, timing, and missing inputs."""

from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest
import yaml

from experiments import ROOT
from experiments.agents.manipulator import Manipulator
from experiments.costs import evasion, measure, summarize
from experiments.data.history import load_history
from experiments.data.synthetic import generate
from experiments.indicators import INDICATORS, evaluate
from experiments.sandbox.cpmm import CpmmPool


def config():
    return yaml.safe_load((ROOT / "config/frozen.yaml").read_text())


def trade_frame(entries):
    pool = CpmmPool(10**24, 10**24)
    rows = []
    for ts, side, amount, wallet in entries:
        f = pool.swap_exact_in(
            side=side,
            amount_in_raw=amount,
            ts=ts,
            actor="manip",
            wallet=wallet,
            quote_usd=1.0,
        )
        rows.append(asdict(f))
    result = pd.DataFrame(rows)
    result["sequence"] = range(len(rows))
    return result


def test_fixed_boundary_differs_from_sliding():
    cfg = config()
    cfg.update(window_hours=1, w_c_hours=1, step_hours=0.5, c_min_share=0.01)
    p = CpmmPool(10**24, 10**24)
    a = p.swap_exact_in(
        side="BUY", amount_in_raw=10**21, ts=3590, actor="manip", wallet="w0"
    )
    b = p.swap_exact_in(
        side="SELL", amount_in_raw=a.amount_out_raw, ts=3610, actor="manip", wallet="w0"
    )
    trades = pd.DataFrame([asdict(a), asdict(b)])
    trades["sequence"] = [0, 1]
    windows = evaluate(trades, ["w0"], [], cfg, 0, 7200, "boundary")
    fixed = windows[(windows.view == "V1") & (windows.indicator == "c_fixed")]
    sliding = windows[(windows.view == "V1") & (windows.indicator == "c_sliding")]
    assert fixed.value.max() == 0
    assert sliding.value.max() == pytest.approx(1)


def test_inactive_controlled_windows_remain():
    cfg = config()
    cfg.update(window_hours=1, w_c_hours=1, step_hours=1)
    trades = trade_frame([(10, "BUY", 10**21, "w0")])
    windows = evaluate(trades, ["w0"], [], cfg, 0, 7200, "silent")
    a = windows[(windows.view == "V1") & (windows.indicator == "a")]
    assert len(a) == 2
    assert list(a.active) == [True, False]
    assert list(a.value) == [1, 0]


def test_risk_uses_initial_and_pre_boundary_marks():
    marks = pd.DataFrame(
        {
            "ts": [0, 0, 1800, 3600, 5400, 7200],
            "equity": [100, 90, 80, 75, 60, 60],
            "inventory_usd": [0] * 6,
        }
    )
    trades = pd.DataFrame({"actor": ["manip"], "notional_usd": [100], "fee_usd": [1]})
    result = measure(trades, marks, dict(risk_window_hours=1, **{"lambda": 1}))
    # [0,3600) changes 100->80, [3600,7200) changes 80->60.
    assert result["R"] == 0
    assert result["S"] == 39
    assert result["c_bps"] == 4000


def test_unknown_e_does_not_become_quiet():
    rows = []
    for indicator in INDICATORS:
        rows.append(
            dict(
                view="V1",
                threshold_pct=95,
                robust=False,
                actor="manip",
                indicator=indicator,
                window_start=0,
                threshold=0.5,
                active=True,
                evaluable=indicator != "e",
                fired=False,
            )
        )
    result = summarize(pd.DataFrame(rows), dict(V=100), dict(run_id="x"))
    assert result.p_e.isna().all()
    assert result.p_a.iloc[0] == 0


def test_all_unavailable_indicator_is_not_infinite():
    row = dict(
        pool="p",
        view="V1",
        threshold_pct=95,
        robust=False,
        scenario="primary",
        **{"lambda": 1},
        V=100,
        c_bps=25,
    )
    for k in INDICATORS:
        row["p_" + k] = np.nan
        row["coverage_" + k] = 0
    result = evasion(pd.DataFrame([row]), dict(V_star_usd=50, epsilon=0.05))
    assert result.delta_c_bps.isna().all()
    assert set(result.status) == {"threshold_or_statistic_unavailable"}


def test_minimum_share_gate_can_be_legitimate_silence():
    row = dict(
        pool="p",
        view="V1",
        threshold_pct=95,
        robust=False,
        scenario="primary",
        **{"lambda": 1},
        V=100,
        c_bps=25,
    )
    for k in INDICATORS:
        row["p_" + k] = 0
        row["coverage_" + k] = 0 if k in ("c_sliding", "c_fixed", "g3") else 1
    result = evasion(pd.DataFrame([row]), dict(V_star_usd=50, epsilon=0.05))
    assert (result.delta_c_bps == 0).all()


def test_guard_checks_recent_opposite_orders():
    from app.compat.volume_strategy.self_trade_guard import IntendedOrder

    spec = dict(name="M6_t60", level=6, alpha=1000, guard=60, band=None)
    trader = Manipulator(spec)
    trader.orders.append(
        IntendedOrder("controlled", trader.config.token_address, "SELL", 100, 100)
    )
    p = CpmmPool(10**24, 10**24)
    assert trader.act(p, 110, 1, 10000, [(100, 1)], [100000, 100000]) == []
    assert trader.decisions[-1]["status"] == "SelfTradeRisk"


def test_runtime_inventory_patch_is_restored():
    from app.compat.volume_strategy import strategy

    original = (
        strategy._V4_SOFT_INVENTORY_LIMIT_USD,
        strategy._V4_HARD_INVENTORY_LIMIT_USD,
    )
    spec = dict(name="M7", level=7, alpha=1000, guard=0, band=[500, 1500])
    trader = Manipulator(spec)
    trader.act(CpmmPool(10**24, 10**24), 100, 1, 10000, [(99, 1)], [100000, 100000])
    assert (
        strategy._V4_SOFT_INVENTORY_LIMIT_USD,
        strategy._V4_HARD_INVENTORY_LIMIT_USD,
    ) == original


def test_empty_blocks_are_required(tmp_path):
    generate(tmp_path, 0, 0.1)
    blocks = pd.read_parquet(tmp_path / "blocks.parquet").drop(index=2)
    blocks.to_parquet(tmp_path / "blocks.parquet", index=False)
    with pytest.raises(ValueError, match="intervening"):
        load_history(tmp_path)


def test_arbitrage_never_changes_volume_share_denominator():
    cfg = config()
    cfg.update(window_hours=1, w_c_hours=1, step_hours=1)
    trades = trade_frame([(10, "BUY", 10**21, "w0"), (20, "SELL", 10**21, "arb")])
    trades.loc[1, "actor"] = "arb"
    windows = evaluate(trades, ["w0"], [], cfg, 0, 3600, "arb")
    a = windows[(windows.view == "V1") & (windows.indicator == "a")]
    assert a.value.iloc[0] == 1
    assert not (windows.entity == "arb").any()


def test_incomplete_target_reaching_grid_is_unidentified():
    rows = []
    for rate in [1, np.nan]:
        row = dict(
            pool="p",
            view="V1",
            threshold_pct=95,
            robust=False,
            scenario="primary",
            **{"lambda": 1},
            V=100,
            c_bps=25,
        )
        for k in INDICATORS:
            row["p_" + k] = rate
            row["coverage_" + k] = 1
        rows.append(row)
    result = evasion(pd.DataFrame(rows), dict(V_star_usd=50, epsilon=0.05))
    assert result.delta_c_bps.isna().all()
    assert set(result.status) == {"incomplete_indicator_grid"}


def test_float_raw_data_and_reserves_are_rejected(tmp_path):
    with pytest.raises(TypeError, match="reserves"):
        CpmmPool(1e24, 10**24)
    generate(tmp_path, 0, 0.1)
    frame = pd.read_parquet(tmp_path / "swaps.parquet")
    frame["amount_in_raw"] = frame.amount_in_raw.astype(float)
    frame.to_parquet(tmp_path / "swaps.parquet", index=False)
    with pytest.raises(ValueError, match="never floats"):
        load_history(tmp_path)
