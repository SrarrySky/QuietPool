import numpy as np
import pandas as pd
import pytest
import yaml

from experiments import ROOT
from experiments.agents.manipulator import ladder_grid
from experiments.costs import measure
from experiments.data.fetch_pool_history import (
    SWAP_TOPIC,
    SYNC_TOPIC,
    CachedReadTransport,
    decode_logs,
)
from experiments.data.history import load_history
from experiments.engine import simulate
from experiments.indicators import evaluate
from experiments.protocol import verify_frozen
from experiments.run import demo_config


def test_freeze_detects_input_mutation(tmp_path):
    folder = tmp_path / "inputs"
    demo_config(folder)
    verify_frozen(folder)
    with (folder / "frozen.yaml").open("a") as stream:
        stream.write("\n# mutation\n")
    with pytest.raises(ValueError, match="changed"):
        verify_frozen(folder)


def test_event_order_and_trader_origin():
    def enc(values):
        return "0x" + "".join(f"{v:064x}" for v in values)

    common = dict(blockNumber="0x1", transactionHash="tx")
    logs = [
        dict(common, logIndex="0x1", topics=[SYNC_TOPIC], data=enc([1100, 910])),
        dict(common, logIndex="0x2", topics=[SWAP_TOPIC], data=enc([100, 0, 0, 90])),
    ]
    result = decode_logs(logs, 0, {1: 100}, {"tx": "0xOrigin"})
    assert result.iloc[0].trader == "0xorigin"
    assert result.iloc[0].reserve_quote_after_raw == 1100
    assert result.iloc[0].side == "BUY"
    with pytest.raises(ValueError, match="preceding Sync"):
        decode_logs(logs[1:], 0, {1: 100}, {"tx": "x"})
    with pytest.raises(ValueError, match="Unmatched Sync"):
        decode_logs(logs[:1], 0, {1: 100}, {"tx": "x"})


def test_read_transport_rejects_unknown_methods(tmp_path):
    t = CachedReadTransport(tmp_path)
    try:
        with pytest.raises(ValueError, match="allowlist"):
            t.post("https://unused", dict(method="not_a_read_method", params=[]))
    finally:
        t.close()


def test_all_ladder_levels_and_reproducibility(tmp_path):
    demo_config(tmp_path / "inputs")
    cfg = yaml.safe_load((tmp_path / "inputs/frozen.yaml").read_text())
    ladder = yaml.safe_load((ROOT / "config/ladder.yaml").read_text())
    cfg.update(horizon_hours=2, window_hours=1, w_c_hours=1, risk_window_hours=0.5)
    meta, swaps, blocks = load_history(tmp_path / "inputs/history_1")
    grid = ladder_grid(ladder)
    assert {s["level"] for s in grid} == set(range(8))
    for spec in grid:
        trades, marks, edges, decisions = simulate(
            meta, swaps, blocks, cfg, ladder, spec, 0
        )
        assert trades.ts.is_monotonic_increasing
        assert (marks[["token_balance", "quote_balance"]] >= -1e-8).all().all()
        assert any(d["status"] == "Filled" for d in decisions)
        costs = measure(trades, marks, cfg)
        assert np.isfinite(costs["c_bps"])
        assert costs["F"] > 0
        assert costs["F"] + costs["S"] == pytest.approx(
            marks.equity.iloc[0] - marks.equity.iloc[-1]
        )
    a = simulate(meta, swaps, blocks, cfg, ladder, grid[0], 0)[0]
    b = simulate(meta, swaps, blocks, cfg, ladder, grid[0], 0)[0]
    pd.testing.assert_frame_equal(a, b)
    # Arb never enters indicator denominators; silent controlled entities persist.
    windows = evaluate(
        a,
        ["w0", "w1"],
        [],
        cfg,
        int(blocks.ts.iloc[0]),
        int(blocks.ts.iloc[0]) + 7200,
        "test",
    )
    assert not windows.entity.str.contains("arb").any()
    assert set(windows.window_kind) == {"sliding", "fixed"}


def test_downloader_end_to_end_with_read_only_fixture(tmp_path, monkeypatch):
    from experiments.data import fetch_pool_history as downloader
    from experiments.sandbox.cpmm import CpmmPool

    quote = "0x55d398326f99059ff775485246999027b3197955"
    token = "0x" + "12" * 20
    pool = CpmmPool(10**24, 10**24)
    fill = pool.swap_exact_in(
        side="BUY", amount_in_raw=10**21, ts=100, actor="organic", wallet="0xOrigin"
    )

    def encode(values):
        return "0x" + "".join(f"{v:064x}" for v in values)

    events = [
        dict(
            blockNumber="0x1",
            transactionHash="tx",
            logIndex="0x1",
            topics=[SYNC_TOPIC],
            data=encode([pool.reserve_quote_raw, pool.reserve_token_raw]),
        ),
        dict(
            blockNumber="0x1",
            transactionHash="tx",
            logIndex="0x2",
            topics=[SWAP_TOPIC],
            data=encode([10**21, 0, 0, fill.amount_out_raw]),
        ),
    ]
    calls = []

    class FixtureSource:
        def __init__(self, url, transport):
            self.transport = transport

        def _call(self, method, params):
            calls.append(method)
            assert method == "eth_chainId"
            return "0x38"

        def get_block(self, n):
            n = 2 if n == "finalized" else n
            return dict(
                number=hex(n),
                timestamp=hex(100 + (n - 1) * 30),
                hash=f"h{n}",
                parentHash=f"h{n - 1}",
            )

        def historical_call(self, *, to, data, block):
            assert block == 0
            if data == "0x0dfe1681":
                return encode([int(quote, 16)])
            if data == "0xd21220a7":
                return encode([int(token, 16)])
            if data == "0x313ce567":
                return encode([18])
            assert data == "0x0902f1ac"
            return encode([10**24, 10**24, 99])

        def get_logs(self, **kwargs):
            return events

        def get_transaction_by_hash(self, tx):
            return {"from": "0xOrigin"}

        def close(self):
            self.transport.close()

    monkeypatch.setattr(downloader, "BnbRpcSource", FixtureSource)
    downloader.fetch("pair", 1, 2, quote, tmp_path, "https://fixture.invalid")
    meta, swaps, blocks = load_history(tmp_path)
    assert meta["source"] == "historical"
    assert len(blocks) == 2 and len(swaps) == 1
    assert swaps.trader.iloc[0] == "0xorigin"
    assert calls == ["eth_chainId"]


def test_no_execution_or_broadcast_calls_in_harness():
    import ast

    forbidden = {
        "sign_transaction",
        "send_raw_transaction",
        "send_transaction",
        "broadcast_transaction",
    }
    for path in ROOT.rglob("*.py"):
        if "vendor" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else ""
                )
                assert name not in forbidden, (path, name)
