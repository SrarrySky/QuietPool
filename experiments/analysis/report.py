"""Paper artifacts with pool-cluster inference and explicit missing evidence.

Generates the manuscript figures, classification tables, sensitivity flips,
organic alert-load and H1/H3 summaries from a completed experiment's outputs.
"""

import os

os.environ.setdefault("MPLCONFIGDIR", "/tmp/redteam-matplotlib")
import matplotlib

matplotlib.use("Agg")
import numpy as np
import pandas as pd

from ..indicators import INDICATORS
from .paper_figures import (
    alarm_heatmap_figure,
    apply_publication_style,
    attribution_figure,
    binding_indicators_figure,
    save_figure,
    timing_cost_figure,
)


def cluster_interval(values, samples=1000, seed=42):
    """Bootstrap a percentile interval over pool-level estimates.

    Each element of "values" is treated as one pool's estimate, so resampling
    draws whole pools rather than seed replicates within a pool.

    Args:
        values (array-like): One estimate per pool; "NaN" entries are dropped.
        samples (int): Number of bootstrap resamples to draw.
        seed (int): Seed for the reproducible random-number generator.

    Returns:
        tuple[float, float]: The 2.5th and 97.5th percentiles of the resampled
        pool means, or "(nan, nan)" when fewer than two pools remain.
    """
    values = np.asarray(values, dtype=float)
    values = values[~np.isnan(values)]
    if len(values) < 2:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    draws = np.mean(
        values[rng.integers(0, len(values), size=(samples, len(values)))], axis=1
    )
    # Nearest rank preserves +infinity without inf-inf interpolation.
    return tuple(np.quantile(draws, [0.025, 0.975], method="inverted_cdf"))


def auc(labels, scores):
    """Compute the rank-based area under the ROC curve.

    Args:
        labels (array-like): Boolean ground-truth labels.
        scores (array-like): Numeric scores aligned with "labels".

    Returns:
        float: Mann-Whitney U statistic normalised by class sizes, or "nan"
        when either class has no observations.
    """
    labels = np.asarray(labels, dtype=bool)
    scores = pd.Series(scores).reset_index(drop=True)
    valid = scores.notna().to_numpy()
    labels, scores = labels[valid], scores[valid]
    n1, n0 = int(labels.sum()), int((~labels).sum())
    if n1 == 0 or n0 == 0:
        return np.nan
    # Rank-based Mann-Whitney U, normalised by the class sizes to [0, 1].
    return float(
        (scores.rank(method="average").to_numpy()[labels].sum() - n1 * (n1 + 1) / 2)
        / (n1 * n0)
    )


def report(
    output,
    summary,
    windows,
    thresholds,
    cfg,
    synthetic,
    organic_counts=None,
    data_root=None,
):
    """Write the paper figures and derived analysis artifacts for a finished run.

    "output" receives newly generated artifacts. "data_root" holds the
    immutable experiment inputs used by plots that need per-run trades or the
    original evasion table; the two coincide during an experiment but differ for
    the standalone "redteam plot" command.

    Args:
        output (pathlib.Path): Directory that receives the generated artifacts.
        summary (pandas.DataFrame): Run-level summary with costs, alarm rates,
            coverage and pool/level/view/scenario identifiers.
        windows (pandas.DataFrame): Window-level indicator statistics and alarms.
        thresholds (pandas.DataFrame): Calibrated indicator thresholds; accepted
            for signature parity and not read by this writer.
        cfg (dict): Frozen configuration ("lambda", "threshold_pcts",
            bootstrap settings, "delta_bps", "epsilon", "V_star_usd").
        synthetic (bool): If True, outputs are marked as synthetic validation.
        organic_counts (pandas.DataFrame | None): Precomputed per-pool organic
            alert load; derived from "windows" when omitted.
        data_root (pathlib.Path | None): Directory holding "evasion.csv" and
            per-run "trades.parquet"; defaults to "output".

    Returns:
        None
    """
    data_root = output if data_root is None else data_root
    apply_publication_style()
    figures = output / "figures"
    figures.mkdir(exist_ok=True)
    title_suffix = " — SYNTHETIC VALIDATION" if synthetic else ""

    from .pipeline import pipeline_figure

    save_figure(pipeline_figure(synthetic), figures, "fig1_pipeline")

    main = summary[
        (summary.scenario == "primary")
        & (summary["lambda"] == cfg["lambda"])
        & (~summary.robust)
    ]
    # Figure 2 preserves equal pool weighting and exposes coverage/target failures.
    for pct in cfg["threshold_pcts"]:
        save_figure(
            alarm_heatmap_figure(main, cfg, pct, title_suffix),
            figures,
            f"fig2_alarm_heatmap_p{pct}",
        )
    # Pool-window volume bound: use the same windows as the share statistic.
    w = windows[
        (windows.actor == "manip")
        & (windows.indicator == "a")
        & (~windows.robust)
        & (windows.scenario == "primary")
        & (windows.view == "V1")
    ].copy()
    w["manufactured_over_organic"] = w.entity_volume / w.organic_volume.replace(
        0, np.nan
    )
    w["bound_over_organic"] = w.threshold / (1 - w.threshold)
    w["bound_holds_when_quiet"] = (w.value > w.threshold) | (
        w.manufactured_over_organic <= w.bound_over_organic + 1e-10
    )
    w.to_csv(output / "volume_bound_windows.csv", index=False)
    save_figure(
        binding_indicators_figure(main, w, cfg, title_suffix),
        figures,
        "fig3_binding_indicators",
    )
    # Table 5: per-pool finite-grid minima, confidence intervals over pools.
    ev = pd.read_csv(data_root / "evasion.csv")
    classifications = []
    for key, g in ev.groupby(
        ["view", "threshold_pct", "robust", "lambda", "scenario", "indicator"]
    ):
        valid = g.delta_c_bps.dropna()
        mean = float(valid.mean()) if len(valid) else np.nan
        low, high = cluster_interval(
            g.delta_c_bps, cfg["bootstrap_samples"], cfg["bootstrap_seed"]
        )
        category = (
            "unresolved"
            if np.isnan(mean) or len(valid) < len(g)
            else (
                "grid-infeasible"
                if np.isinf(mean)
                else "cosmetic"
                if mean <= cfg["delta_bps"]
                else "costly-on-grid"
            )
        )
        classifications.append(
            dict(
                zip(
                    [
                        "view",
                        "threshold_pct",
                        "robust",
                        "lambda",
                        "scenario",
                        "indicator",
                    ],
                    key,
                    strict=False,
                ),
                classification=category,
                delta_c_bps=mean,
                ci_low=low,
                ci_high=high,
                n_pools=len(g),
                n_identified_pools=len(valid),
                n_grid_infeasible_pools=int(np.isinf(g.delta_c_bps).sum()),
                max_quiet_volume=float(g.max_quiet_volume.mean()),
            )
        )
    table = pd.DataFrame(classifications)
    void = table.drop_duplicates(
        ["view", "threshold_pct", "robust", "lambda", "scenario"]
    ).copy()
    void["indicator"] = "f"
    void["classification"] = "void"
    void[["delta_c_bps", "ci_low", "ci_high", "max_quiet_volume"]] = np.nan
    table = pd.concat([table, void], ignore_index=True)
    table.to_csv(output / "table5_classification.csv", index=False)
    primary = table[
        (table.scenario == "primary")
        & (table["lambda"] == cfg["lambda"])
        & (~table.robust)
    ]
    primary.to_latex(
        output / "table5_classification.tex",
        index=False,
        float_format="%.3f",
        escape=True,
    )
    # Retain analytically void indicator f independently of empirical estimates.
    (output / "indicator_scope.csv").write_text(
        "indicator,scope,reason\nf,void,No resting order book\ng,out_of_scope,Oracle manipulation is outside the trading experiment\n"
    )
    # Sensitivity flips always compare matching views/thresholds/risks/indicators.
    reference = (
        table[table.scenario == "primary"]
        .drop(columns="scenario")[
            ["view", "threshold_pct", "robust", "lambda", "indicator", "classification"]
        ]
        .rename(columns={"classification": "primary_classification"})
    )
    flips = table.merge(
        reference, on=["view", "threshold_pct", "robust", "lambda", "indicator"]
    )
    flips["classification_changed"] = (
        flips.classification != flips.primary_classification
    )
    flips.to_csv(output / "classification_sensitivity.csv", index=False)
    # Organic alert load is descriptive, not a false-positive rate.
    if organic_counts is not None:
        per_pool = organic_counts
    else:
        organic = windows[windows.actor == "organic"]
        per_pool = (
            organic.groupby(
                ["pool", "view", "threshold_pct", "robust", "scenario", "indicator"]
            )
            .agg(alert_load=("fired", "mean"), coverage=("evaluable", "mean"))
            .reset_index()
        )
    per_pool.to_csv(output / "organic_alert_load.csv", index=False)
    load_rows = []
    for key, g in per_pool.groupby(
        ["view", "threshold_pct", "robust", "scenario", "indicator"]
    ):
        low, high = cluster_interval(
            g.alert_load, cfg["bootstrap_samples"], cfg["bootstrap_seed"]
        )
        load_rows.append(
            dict(
                zip(
                    ["view", "threshold_pct", "robust", "scenario", "indicator"],
                    key,
                    strict=False,
                ),
                alert_load=g.alert_load.mean(),
                ci_low=low,
                ci_high=high,
                n_pools=len(g),
            )
        )
    pd.DataFrame(load_rows).to_csv(output / "organic_alert_load_ci.csv", index=False)
    # H1: raw statistics once per run, never duplicate threshold/robustness rows.
    c = windows[
        (windows.indicator == "c_sliding")
        & (windows.threshold_pct == 95)
        & (~windows.robust)
        & (windows.scenario == "primary")
    ]
    auc_rows = []
    for (pool, view, level), g in c.groupby(["pool", "view", "level"]):
        auc_rows.append(
            dict(
                pool=pool, view=view, level=level, auc=auc(g.actor == "manip", g.value)
            )
        )
    auc_table = pd.DataFrame(auc_rows)
    auc_table.to_csv(output / "h1_auc.csv", index=False)
    auc_ci = []
    for (view, level), group in auc_table.groupby(["view", "level"]):
        low, high = cluster_interval(
            group.auc, cfg["bootstrap_samples"], cfg["bootstrap_seed"]
        )
        auc_ci.append(
            dict(
                view=view,
                level=level,
                auc=group.auc.mean(),
                ci_low=low,
                ci_high=high,
                n_pools=len(group),
            )
        )
    pd.DataFrame(auc_ci).to_csv(output / "h1_auc_ci.csv", index=False)
    save_figure(
        attribution_figure(c, auc_table, title_suffix),
        figures,
        "h1_attribution_distributions",
    )
    # H3: both timing proxies, with non-finite outcomes retained explicitly.
    h3 = []
    for key, g in main.groupby(["pool", "view", "threshold_pct"]):
        for indicator in ("e", "g2"):
            feasible = g[
                (g[f"p_{indicator}"] <= cfg["epsilon"])
                & (g[f"coverage_{indicator}"] > 0)
                & (g.V >= cfg["V_star_usd"])
                & g.c_bps.notna()
            ]
            tau = np.nan
            if indicator == "e" and not feasible.empty:
                selected = feasible.loc[feasible.c_bps.idxmin()]
                trades = pd.read_parquet(data_root / selected.run_id / "trades.parquet")
                times = (
                    trades.loc[trades.actor == "manip", "ts"]
                    .drop_duplicates()
                    .to_numpy()
                )
                tau = float(np.median(np.diff(times))) if len(times) > 1 else np.nan
            found = ev[
                (ev.pool == key[0])
                & (ev.view == key[1])
                & (ev.threshold_pct == key[2])
                & (~ev.robust)
                & (ev["lambda"] == cfg["lambda"])
                & (ev.scenario == "primary")
                & (ev.indicator == indicator)
            ]
            delta = found.delta_c_bps.iloc[0] if len(found) else np.nan
            status = found.status.iloc[0] if len(found) else "missing_evasion_row"
            h3.append(
                dict(
                    pool=key[0],
                    view=key[1],
                    threshold_pct=key[2],
                    indicator=indicator,
                    volatility=g.volatility.iloc[0],
                    tau_seconds=tau,
                    prediction_bps=cfg["lambda"]
                    * g.volatility.iloc[0]
                    * np.sqrt(tau)
                    / 2
                    * 10000,
                    delta_c_bps=delta,
                    status=status,
                )
            )
    h3 = pd.DataFrame(h3)
    pool_volatility = h3.groupby("pool").volatility.first()
    terciles = pd.qcut(
        pool_volatility.rank(method="dense"), 3, labels=False, duplicates="drop"
    )
    h3["volatility_tercile"] = h3.pool.map(terciles)
    h3.to_csv(output / "h3_timing.csv", index=False)
    save_figure(
        timing_cost_figure(h3, cfg, title_suffix),
        figures,
        "h3_timing_cost",
    )
    # Window-level rates pooled within pools, intervals resampling only pools.
    rate_rows = []
    for key, g in summary.groupby(
        ["level", "view", "threshold_pct", "robust", "lambda", "scenario"]
    ):
        for metric in ["c_bps"] + ["p_" + x for x in INDICATORS]:
            values = g.groupby("pool")[metric].mean()
            low, high = cluster_interval(
                values, cfg["bootstrap_samples"], cfg["bootstrap_seed"]
            )
            rate_rows.append(
                dict(
                    zip(
                        [
                            "level",
                            "view",
                            "threshold_pct",
                            "robust",
                            "lambda",
                            "scenario",
                        ],
                        key,
                        strict=False,
                    ),
                    metric=metric,
                    mean=values.mean(),
                    ci_low=low,
                    ci_high=high,
                    n_pools=len(values),
                )
            )
    pd.DataFrame(rate_rows).to_csv(output / "pool_cluster_intervals.csv", index=False)
    (output / "REPORT.md").write_text(
        "# Experiment report\n\n"
        + (
            "**SYNTHETIC VALIDATION ONLY. These numbers are not empirical paper results.**\n\n"
            if synthetic
            else ""
        )
        + "All figures are available as PDF, SVG, and PNG. Table 5 uses finite-grid classifications, not proofs of universal binding. Infinity denotes no feasible tested configuration at the target; NaN denotes unidentified evidence. Confidence intervals resample pools, never trades or seeds. Organic alert load is not a false-positive rate. See the source experiment manifest and protocol lock for provenance.\n"
    )
