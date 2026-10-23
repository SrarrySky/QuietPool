"""Evidence sufficiency diagnostics; never a publication-acceptance promise.

Screens a completed run against the study's sample-size and coverage
preconditions and emits finite-only H3 association diagnostics.
"""

import json

import numpy as np
import pandas as pd


def assess(output, summary, pools, cfg, synthetic):
    """Screen evidence sufficiency and write readiness and H3 diagnostics.

    Args:
        output (pathlib.Path): Output directory; receives
            "study_readiness.json" and reads "evasion.csv".
        summary (pandas.DataFrame): Run-level summary rows.
        pools (dict): Pool registry whose "pools" entry lists per-pool records
            with a "split" field.
        cfg (dict): Frozen configuration with "window_hours", "w_c_hours",
            "horizon_hours", "bootstrap_seed" and "bootstrap_samples".
        synthetic (bool): If True, mark the readiness status as synthetic
            validation only.

    Returns:
        None
    """
    test = summary[summary.scenario == "primary"].pool.nunique()
    calibration = sum(p["split"] == "calibration" for p in pools["pools"])
    checks = {
        "historical_data": not synthetic,
        "at_least_12_independent_test_pools": test >= 12,
        "calibration_test_ratio_1_to_2": test == 2 * calibration,
        "daily_windows": cfg["window_hours"] == 24 and cfg["w_c_hours"] == 24,
        "at_least_72_hours": cfg["horizon_hours"] >= 72,
        "all_eight_levels": set(summary.level.str.extract(r"^(M\d+)")[0])
        == {f"M{i}" for i in range(8)},
        "no_missing_primary_alarm_rates": not summary.loc[
            summary.scenario == "primary", [c for c in summary if c.startswith("p_")]
        ]
        .isna()
        .any()
        .any(),
    }
    report = dict(
        status="synthetic_validation_only"
        if synthetic
        else "requires_scientific_review",
        checks=checks,
        independent_test_pools=int(test),
        calibration_pools=calibration,
        note="The sample-size check is an engineering screening threshold, not a power calculation or a guarantee of publication. Do not infer H1-H4 from synthetic results. Organic funding attribution and finite-grid limitations remain.",
    )
    (output / "study_readiness.json").write_text(json.dumps(report, indent=2) + "\n")
    ev = pd.read_csv(output / "evasion.csv")
    primary = ev[
        (ev.scenario == "primary") & (ev["lambda"] == cfg["lambda"]) & (~ev.robust)
    ]
    volatility = summary[["pool", "volatility"]].drop_duplicates("pool")
    rows = []
    for (view, pct, indicator), g in primary[
        primary.indicator.isin(["e", "g2"])
    ].groupby(["view", "threshold_pct", "indicator"]):
        g = g.merge(volatility, on="pool", validate="one_to_one")
        finite = g[np.isfinite(g.delta_c_bps) & np.isfinite(g.volatility)]
        rho, pvalue = np.nan, np.nan
        if (
            len(finite) >= 6
            and finite.delta_c_bps.nunique() > 1
            and finite.volatility.nunique() > 1
        ):
            x = finite.volatility.rank().to_numpy()
            y = finite.delta_c_bps.rank().to_numpy()
            rho = float(np.corrcoef(x, y)[0, 1])
            rng = np.random.default_rng(cfg["bootstrap_seed"])
            trials = cfg["bootstrap_samples"]
            exceed = sum(
                abs(np.corrcoef(x, rng.permutation(y))[0, 1]) >= abs(rho)
                for _ in range(trials)
            )
            pvalue = (exceed + 1) / (trials + 1)
        rows.append(
            dict(
                view=view,
                threshold_pct=pct,
                indicator=indicator,
                n_pools=len(g),
                n_finite=len(finite),
                n_infinite=int(np.isinf(g.delta_c_bps).sum()),
                rho_finite_only=rho,
                permutation_p_unadjusted=pvalue,
            )
        )
    pd.DataFrame(rows).to_csv(output / "h3_association_diagnostics.csv", index=False)
