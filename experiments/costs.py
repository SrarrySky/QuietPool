"""Dollar accounting and target-conditioned finite-grid evasion costs."""

import numpy as np
import pandas as pd

from .indicators import INDICATORS


def measure(trades, marks, cfg):
    """Compute per-path economic outcomes and the risk-adjusted evasion cost.

    Aggregates one replay path's manipulated volume, fees, wealth loss, one-hour
    risk, and the resulting unit cost in basis points.

    Args:
        trades: DataFrame of :class:`SwapFill` records for one replay path.
        marks: DataFrame of per-block marked-wealth snapshots.
        cfg: Frozen configuration mapping (see "frozen.yaml").

    Returns:
        Dict with keys "V", "F", "S", "R", "c_bps",
        "max_abs_inventory_usd", "max_abs_inventory_change_usd",
        "passive_pnl_usd", "incremental_pnl_usd", and "pool_vol_daily".
    """
    manip = trades[trades.actor == "manip"]
    volume = float(manip.notional_usd.sum())
    fees = float(manip.fee_usd.sum())
    loss = float(marks.equity.iloc[0] - marks.equity.iloc[-1])
    # Equal-duration endpoint changes, not uneven per-trade observations.
    endpoints = np.arange(
        marks.ts.iloc[0], marks.ts.iloc[-1] + 1, int(cfg["risk_window_hours"] * 3600)
    )
    # A window [a,b) ends immediately before trades stamped b.
    indices = np.maximum(
        0, np.searchsorted(marks.ts.to_numpy(), endpoints, side="left") - 1
    )
    indices[0] = 0
    changes = np.diff(marks.equity.to_numpy()[indices])
    risk = float(np.std(changes, ddof=1)) if len(changes) > 1 else np.nan
    return dict(
        V=volume,
        F=fees,
        S=loss - fees,
        R=risk,
        c_bps=(loss + cfg["lambda"] * risk) / volume * 10000
        if volume and np.isfinite(risk)
        else np.nan,
        max_abs_inventory_usd=float(marks.inventory_usd.abs().max()),
        max_abs_inventory_change_usd=float(marks.inventory_change_usd.abs().max())
        if "inventory_change_usd" in marks
        else np.nan,
        passive_pnl_usd=float(
            marks.passive_equity.iloc[-1] - marks.passive_equity.iloc[0]
        )
        if "passive_equity" in marks
        else np.nan,
        incremental_pnl_usd=float(marks.incremental_equity.iloc[-1])
        if "incremental_equity" in marks
        else np.nan,
        pool_vol_daily=float(
            trades.loc[trades.actor == "organic", "notional_usd"].sum()
        )
        / ((marks.ts.iloc[-1] - marks.ts.iloc[0]) / 86400),
    )


def summarize(windows, costs, metadata):
    """Aggregate per-window indicator firing into per-view/threshold rows.

    For each (view, threshold percentile, robustness variant) group this
    computes the fraction of sampled windows that fired each indicator and the
    corresponding evaluation coverage.

    Args:
        windows: DataFrame of evaluated indicator windows (from
            :func:`indicators.evaluate`).
        costs: Dict of economic outcomes from :func:`measure`.
        metadata: Dict of run-level keys to attach to every output row.

    Returns:
        DataFrame with one row per (view, threshold_pct, robust) group and one
        column per indicator ("p_<indicator>", "coverage_<indicator>").
    """
    rows = []
    for (view, pct, robust), group in windows.groupby(
        ["view", "threshold_pct", "robust"]
    ):
        row = dict(**metadata, view=view, threshold_pct=pct, robust=robust, **costs)
        for indicator in INDICATORS:
            w = group[(group.actor == "manip") & (group.indicator == indicator)]
            # Alarm on any controlled cluster in each time window.
            by = w.groupby("window_start")
            row["p_" + indicator] = (
                float(by.fired.any().mean())
                if len(w) and w.threshold.notna().all()
                else np.nan
            )
            row["coverage_" + indicator] = (
                float(by.evaluable.any().mean()) if len(w) else 0.0
            )
            # c'/G3 minimum-share gating and absent entities are legitimate silence.
            # An active e window lacking a complete forward horizon is unknown.
            if indicator == "e" and ((w.active) & (~w.evaluable)).any():
                row["p_e"] = np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def evasion(summary, cfg):
    """Compute grid-relative evasion cost deltas for every indicator.

    For each (pool, view, percentile, robustness, lambda, scenario) group this
    finds the cheapest target-reaching configuration (baseline) and the cheapest
    quiet target-reaching configuration, reporting their cost difference and the
    classification status (feasible, infeasible, or unidentified).

    Args:
        summary: DataFrame produced by :func:`summarize`.
        cfg: Frozen configuration mapping.

    Returns:
        DataFrame with one row per group/indicator, including "delta_c_bps",
        "status", "max_quiet_volume", "baseline_c_bps", and candidate
        counts.
    """
    rows = []
    keys = ["pool", "view", "threshold_pct", "robust", "lambda", "scenario"]
    for key, group in summary.groupby(keys):
        enough = group[(group.V >= cfg["V_star_usd"]) & group.c_bps.notna()]
        baseline = enough.c_bps.min()
        for indicator in INDICATORS:
            quiet = group[group["p_" + indicator] <= cfg["epsilon"]]
            feasible = quiet[(quiet.V >= cfg["V_star_usd"]) & quiet.c_bps.notna()]
            if group["p_" + indicator].isna().all():
                delta, status = np.nan, "threshold_or_statistic_unavailable"
            elif enough.empty:
                delta, status = np.nan, "target_unreached"
            elif enough["p_" + indicator].isna().any():
                delta, status = np.nan, "incomplete_indicator_grid"
            elif feasible.empty:
                delta, status = np.inf, "grid_infeasible"
            else:
                delta, status = float(feasible.c_bps.min() - baseline), "feasible"
            rows.append(
                dict(
                    zip(keys, key, strict=False),
                    indicator=indicator,
                    delta_c_bps=delta,
                    status=status,
                    max_quiet_volume=float(quiet.V.max()) if len(quiet) else 0.0,
                    baseline_c_bps=baseline,
                    n_candidates=len(group),
                    n_evaluated_candidates=int(group["p_" + indicator].notna().sum()),
                )
            )
    return pd.DataFrame(rows)
