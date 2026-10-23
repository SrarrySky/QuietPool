"""Numerical equivalence and explicit resource admission tests."""

import time

import numpy as np
import pandas as pd
import pytest
import yaml

from experiments import ROOT
from experiments.indicators import reversion_weights
from experiments.resources import Budget, BudgetExceeded, plan
from experiments.run import demo_config


def test_rpc_cache_resumes_without_spending_request_budget(tmp_path, monkeypatch):
    from experiments.data.fetch_pool_history import CachedReadTransport

    transport = CachedReadTransport(tmp_path, max_requests=1, requests_per_second=1e9)
    monkeypatch.setattr(transport.transport, "post", lambda *args: {"result": "0xbeef"})
    payload = dict(method="eth_getBlockByNumber", params=["0x1", False])
    try:
        assert transport.post("https://fixture.invalid", payload)["result"] == "0xbeef"
        assert transport.post("https://fixture.invalid", payload)["result"] == "0xbeef"
        assert (transport.requests, transport.cache_hits) == (1, 1)
        with pytest.raises(RuntimeError, match="request budget"):
            transport.post(
                "https://fixture.invalid", dict(method="eth_chainId", params=[])
            )
    finally:
        transport.close()


def test_rpc_finality_tags_are_never_cached(tmp_path, monkeypatch):
    from experiments.data.fetch_pool_history import CachedReadTransport

    transport = CachedReadTransport(tmp_path, requests_per_second=1e9)
    monkeypatch.setattr(
        transport.transport, "post", lambda *args: {"result": transport.requests}
    )
    payload = dict(method="eth_getBlockByNumber", params=["finalized", False])
    try:
        assert transport.post("https://fixture.invalid", payload)["result"] == 1
        assert transport.post("https://fixture.invalid", payload)["result"] == 2
        assert not list(tmp_path.glob("*.json"))
    finally:
        transport.close()


def test_rpc_cache_budget_stops_before_writing(tmp_path, monkeypatch):
    from experiments.data.fetch_pool_history import CachedReadTransport

    transport = CachedReadTransport(tmp_path, cache_mb=0.000001)
    monkeypatch.setattr(transport.transport, "post", lambda *args: {"result": "0xbeef"})
    try:
        with pytest.raises(RuntimeError, match="cache budget"):
            transport.post(
                "https://fixture.invalid", dict(method="eth_call", params=[])
            )
        assert not list(tmp_path.glob("*.json"))
    finally:
        transport.close()


def test_linear_reversion_matches_brute_force():
    rng = np.random.default_rng(42)
    for n in [1, 2, 30, 500]:
        ts = np.cumsum(rng.integers(0, 10, n))
        before = np.exp(rng.normal(0, 0.1, n))
        after = before * np.exp(rng.normal(0, 0.01, n))
        frame = pd.DataFrame(dict(ts=ts, price_before=before, price_after=after))
        recovered, weights = reversion_weights(frame, 30, int(ts[-1]))
        for i in range(n):
            impact = np.log(after[i] / before[i])
            expected_weight = (
                abs(impact) if ts[i] + 30 <= ts[-1] and abs(impact) >= 1e-15 else 0
            )
            future = np.log(after[i + 1 :][ts[i + 1 :] <= ts[i] + 30])
            back = (
                max(0, float(np.max(-np.sign(impact) * (future - np.log(after[i])))))
                if len(future)
                else 0
            )
            expected_recovered = min(expected_weight, back) if expected_weight else 0
            assert weights[i] == pytest.approx(expected_weight, abs=1e-15)
            assert recovered[i] == pytest.approx(expected_recovered, abs=1e-15)


def test_plan_checks_run_count_before_execution(tmp_path):
    folder = tmp_path / "inputs"
    demo_config(folder, hours=2)
    cfg = yaml.safe_load((folder / "frozen.yaml").read_text())
    ladder = yaml.safe_load((ROOT / "config/ladder.yaml").read_text())
    pools = yaml.safe_load((folder / "pools.yaml").read_text())
    result = plan(folder, cfg, ladder, pools, True)
    assert result["runs"] == 210
    cfg["resources"]["max_runs"] = 100
    with pytest.raises(BudgetExceeded, match="210 runs"):
        plan(folder, cfg, ladder, pools, True)


def test_budget_disk_and_wall_limits(tmp_path):
    budget = Budget(dict(disk_mb=0.001, wall_seconds=0.001), tmp_path)
    with pytest.raises(BudgetExceeded, match="output budget"):
        budget.reserve(10000)
    budget.started = time.monotonic() - 1
    with pytest.raises(BudgetExceeded, match="Wall-time"):
        budget.check()


def test_budget_rows_and_incomplete_marker(tmp_path):
    budget = Budget(dict(memory_mb=4096, max_indicator_rows=2), tmp_path)
    with pytest.raises(BudgetExceeded, match="row budget"), budget:
        budget.check(rows=3)
    assert (tmp_path / "INCOMPLETE.json").exists()
