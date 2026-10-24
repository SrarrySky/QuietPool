"""Supplementary analysis: General Player (cumulative ladder) vs Top Player.

The main experiment varies a cumulative ladder (M0--M7) that stacks controls;
the supplementary experiment replaces it with a directed grid where each spec
deviates one factor at a time toward a declared MAR statistic. This module
contrasts the two grids so the reader sees whether a smarter, threshold-aware
trader ("Top Player") can dodge the wash-trading indicators that the main grid
leaves grid-infeasible.
"""

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

INDICATORS = ["a", "b", "c_sliding", "c_fixed", "d", "e", "g1", "g2", "g3"]

# The directed spec that targets each wash-trading indicator.
TARGET_SPEC = {
    "a": "TP_a",
    "b": "TP_b",
    "c_sliding": "TP_c",
    "c_fixed": "TP_c",
    "d": "TP_d",
    "e": "TP_e",
    "g1": "TP_g1",
}


def _primary(summary, cfg):
    return summary[
        (summary.scenario == "primary")
        & (summary["lambda"] == cfg["lambda"])
        & (~summary.robust)
    ]


def _pool_mean(summary, level, column):
    """Pool-equal mean of a statistic across the given level, or NaN."""
    rows = summary[summary.level == level]
    if rows.empty or rows[column].isna().all():
        return np.nan
    return float(rows[column].mean())


def _alarm_row(job):
    """Compute one indicator's General vs Top Player alarm-rate contrast."""
    indicator = job["indicator"]
    column = "p_" + indicator
    main = job["main"]
    sup = job["sup"]
    spec = TARGET_SPEC.get(indicator)
    general_m0 = _pool_mean(main, "M0", column)
    general_best = (
        main.loc[main[column].notna(), [column, "level"]]
        .groupby("level")[column]
        .mean()
        .min()
        if main[column].notna().any()
        else np.nan
    )
    top_player = _pool_mean(sup, spec, column) if spec else np.nan
    top_best = (
        sup.loc[sup[column].notna(), [column, "level"]]
        .groupby("level")[column]
        .mean()
        .min()
        if sup[column].notna().any()
        else np.nan
    )
    return dict(
        indicator=indicator,
        target_spec=spec,
        general_M0_p=general_m0,
        general_grid_min_p=general_best,
        top_player_p=top_player,
        top_grid_min_p=top_best,
        reduction_vs_M0=(
            general_m0 - top_player
            if np.isfinite(general_m0) and np.isfinite(top_player)
            else np.nan
        ),
    )


def compare(supplementary_output, main_output, cfg, jobs=1):
    """Write General vs Top Player contrast tables into "supplementary_output".

    Args:
        supplementary_output (pathlib.Path): Supplementary run output directory
            (holds its own "summary.csv" and "evasion.csv").
        main_output (pathlib.Path): Main run output directory for the General
            Player baseline ("summary.csv" and "evasion.csv").
        cfg (dict): Frozen configuration ("lambda", "epsilon", "V_star_usd").
        jobs (int): Worker processes for the indicator comparison (1 = serial).

    Returns:
        None

    Raises:
        ValueError: If "jobs" is not a positive integer.
    """
    if jobs < 1:
        raise ValueError("jobs must be a positive integer (1 means serial)")
    supplementary_output = Path(supplementary_output)
    main_output = Path(main_output)
    sup = _primary(pd.read_csv(supplementary_output / "summary.csv"), cfg)
    main = _primary(pd.read_csv(main_output / "summary.csv"), cfg)

    work = [dict(indicator=i, main=main, sup=sup) for i in INDICATORS]
    executor = ProcessPoolExecutor(max_workers=jobs) if jobs > 1 else None
    try:
        if executor is not None:
            rows = list(executor.map(_alarm_row, work))
        else:
            rows = [_alarm_row(job) for job in work]
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
    pd.DataFrame(rows).to_csv(
        supplementary_output / "supplementary_alarm_comparison.csv", index=False
    )

    ev_main = pd.read_csv(main_output / "evasion.csv")
    ev_sup = pd.read_csv(supplementary_output / "evasion.csv")
    ev_main = ev_main[
        (ev_main.scenario == "primary")
        & (ev_main["lambda"] == cfg["lambda"])
        & (~ev_main.robust)
        & (ev_main.indicator.isin(INDICATORS))
    ]
    ev_sup = ev_sup[
        (ev_sup.scenario == "primary")
        & (ev_sup["lambda"] == cfg["lambda"])
        & (~ev_sup.robust)
        & (ev_sup.indicator.isin(INDICATORS))
    ]

    def _classify(frame):
        out = []
        for indicator, g in frame.groupby("indicator"):
            valid = g.delta_c_bps.dropna()
            mean = float(valid.mean()) if len(valid) else np.nan
            if np.isnan(mean):
                category = "unresolved"
            elif np.isinf(mean):
                category = "grid-infeasible"
            elif mean <= cfg["delta_bps"]:
                category = "cosmetic"
            else:
                category = "costly-on-grid"
            out.append(
                dict(
                    indicator=indicator,
                    delta_c_bps=mean,
                    category=category,
                    n_grid_infeasible_pools=int(np.isinf(g.delta_c_bps).sum()),
                    max_quiet_volume=float(g.max_quiet_volume.mean()),
                )
            )
        return pd.DataFrame(out)

    main_cls = _classify(ev_main).rename(
        columns={
            "delta_c_bps": "main_delta_c_bps",
            "category": "main_category",
            "n_grid_infeasible_pools": "main_n_grid_infeasible_pools",
            "max_quiet_volume": "main_max_quiet_volume",
        }
    )
    sup_cls = _classify(ev_sup).rename(
        columns={
            "delta_c_bps": "top_delta_c_bps",
            "category": "top_category",
            "n_grid_infeasible_pools": "top_n_grid_infeasible_pools",
            "max_quiet_volume": "top_max_quiet_volume",
        }
    )
    pd.merge(main_cls, sup_cls, on="indicator", how="outer").to_csv(
        supplementary_output / "supplementary_evasion_comparison.csv", index=False
    )

    report = dict(
        title="General Player vs Top Player",
        epsilon=cfg["epsilon"],
        V_star_usd=cfg["V_star_usd"],
        delta_bps=cfg["delta_bps"],
        note=(
            "Top Player specs deviate one factor at a time toward a declared MAR "
            "statistic. A reduction in an alarm rate below the general baseline, or a "
            "status change from grid-infeasible to cosmetic/feasible, is evidence that a "
            "threshold-aware trader can dodge the statistic. TP_b and TP_c/TP_d pull in "
            "opposite directions, so no single path silences every wash-trading statistic."
        ),
    )
    (supplementary_output / "supplementary_report.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main", required=True, help="Main run output directory")
    parser.add_argument(
        "--supplementary", required=True, help="Supplementary run output directory"
    )
    parser.add_argument("--config", required=True, help="Supplementary config directory")
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Worker processes for the indicator comparison (default 1 = serial)",
    )
    args = parser.parse_args()

    import yaml

    cfg = yaml.safe_load((Path(args.config) / "frozen.yaml").read_text())
    compare(Path(args.supplementary), Path(args.main), cfg, args.jobs)
    print(f"Wrote General vs Top Player contrasts into {args.supplementary}")


if __name__ == "__main__":
    main()
