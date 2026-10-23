"""CLI: demo, plan, freeze, run, plot."""

import argparse
import gzip
import json
import platform
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from importlib.metadata import version
from pathlib import Path

import pandas as pd
import yaml

from . import ROOT
from .agents.manipulator import ladder_grid
from .costs import evasion, measure, summarize
from .data.history import load_history, write_frame
from .data.synthetic import generate
from .engine import realized_volatility, simulate
from .indicators import apply_thresholds, calibrate, evaluate
from .protocol import freeze, load_config, sha256, verify_frozen
from .resources import Budget, plan


def run(config, output, sensitivities=False, jobs=1):
    """Execute the full experiment from a frozen protocol.

    Args:
        config: Path to the experiment config directory.
        output: Empty output directory to write results into.
        sensitivities: Whether to also run sensitivity scenarios.
        jobs: Number of worker processes (1 = serial).

    Returns:
        The consolidated summary DataFrame.

    Raises:
        ValueError: If "jobs" is invalid or "output" is non-empty.
    """
    if jobs < 1:
        raise ValueError("jobs must be a positive integer (1 means serial)")
    cfg, ladder, pools = load_config(config)
    planned = plan(config, cfg, ladder, pools, sensitivities)
    if Path(output).exists() and any(Path(output).iterdir()):
        raise ValueError("Output directory must be empty")
    with Budget(planned["limits"], output) as budget:
        return _run(config, output, sensitivities, budget, planned, jobs)


def alarm_counts(frame):
    """Aggregate organic firing counts per pool/view/threshold/scenario/indicator."""
    return (
        frame[frame.actor == "organic"]
        .groupby(["pool", "view", "threshold_pct", "robust", "scenario", "indicator"])
        .agg(
            n=("fired", "size"), alarms=("fired", "sum"), evaluated=("evaluable", "sum")
        )
        .reset_index()
    )


def aggregate_counts(frames):
    """Combine alarm counts across pools and add alert-load/coverage ratios."""
    counts = (
        pd.concat(frames, ignore_index=True)
        .groupby(["pool", "view", "threshold_pct", "robust", "scenario", "indicator"])[
            ["n", "alarms", "evaluated"]
        ]
        .sum()
        .reset_index()
    )
    counts["alert_load"] = counts.alarms / counts.n
    counts["coverage"] = counts.evaluated / counts.n
    return counts


def _apply_depth(meta, current):
    """Scale pool reserves by the configured depth multiplier."""
    meta = meta.copy()
    for column in ("reserve_quote_raw", "reserve_token_raw"):
        meta[column] = str(int(int(meta[column]) * current.get("depth_multiplier", 1)))
    return meta


def _calibration_pool(job):
    """Compute one calibration pool's organic-only indicator windows.

    The worker-local budget enforces only wall, memory, and row limits; shared
    disk admission is deferred to the coordinator.

    Args:
        job: Mapping with "limits", "output", "started", "history",
            "current", "ladder", and "pool_id" keys.

    Returns:
        DataFrame of indicator windows tagged with the pool id.
    """
    budget = Budget(
        job["limits"], job["output"], started=job["started"], enforce_disk=False
    )
    meta, swaps, blocks = load_history(job["history"])
    meta = _apply_depth(meta, job["current"])
    trades, marks, _, _ = simulate(
        meta, swaps, blocks, job["current"], job["ladder"], budget=budget
    )
    windows = evaluate(
        trades,
        [],
        [],
        job["current"],
        int(marks.ts.iloc[0]),
        int(marks.ts.iloc[-1]),
        job["pool_id"] + "_calibration",
        budget=budget,
    )
    windows["pool"] = job["pool_id"]
    return windows


def _test_pool_job(job):
    """Run every strategy/seed for one test pool and return reduced frames.

    Args:
        job: Mapping with "limits", "output", "started", "history",
            "current", "ladder", "pool_id", "scenario", and
            "thresholds" keys.

    Returns:
        Dict with "baseline_counts", "summary_frames",
        "organic_count_frames", "window_frames", and "activity_rows".
    """
    budget = Budget(
        job["limits"], job["output"], started=job["started"], enforce_disk=False
    )
    current = job["current"]
    ladder = job["ladder"]
    pool_id = job["pool_id"]
    scenario = job["scenario"]
    output = job["output"]
    meta, swaps, blocks = load_history(job["history"])
    meta = _apply_depth(meta, current)
    volatility = realized_volatility(swaps, meta.get("liquidity_events", []))
    background, background_marks, _, _ = simulate(
        meta, swaps, blocks, current, ladder, budget=budget
    )
    baseline_windows = evaluate(
        background,
        [],
        [],
        current,
        int(background_marks.ts.iloc[0]),
        int(background_marks.ts.iloc[-1]),
        pool_id + "_organic_only_" + scenario,
        budget=budget,
    )
    baseline_windows["pool"] = pool_id
    baseline_fired = apply_thresholds(baseline_windows, job["thresholds"])
    write_frame(
        baseline_fired,
        output / f"organic_only_{pool_id}_{scenario}.parquet",
        budget=budget,
    )
    baseline_counts = alarm_counts(baseline_fired)

    summary_frames = []
    organic_count_frames = []
    window_frames = []
    activity_rows = []
    for spec in ladder_grid(ladder):
        for seed in current["seeds"]:
            run_id = f"{pool_id}_{spec['name']}_{seed}_{scenario}"
            directory = output / run_id
            directory.mkdir()
            trades, marks, edges, decisions = simulate(
                meta, swaps, blocks, current, ladder, spec, seed, budget=budget
            )
            windows = evaluate(
                trades,
                [f"w{i}" for i in range(ladder["wallets"])],
                edges,
                current,
                int(marks.ts.iloc[0]),
                int(marks.ts.iloc[-1]),
                run_id,
                budget=budget,
            )
            windows["pool"] = pool_id
            fired = apply_thresholds(windows, job["thresholds"])
            fired["level"] = spec["name"]
            write_frame(trades, directory / "trades.parquet", budget=budget)
            write_frame(marks, directory / "marks.parquet", budget=budget)
            write_frame(fired, directory / "indicator_windows.parquet", budget=budget)
            (directory / "funding_edges.json").write_text(
                json.dumps(edges, indent=2) + "\n"
            )
            accepted = [d for d in decisions if d["status"] == "Filled"]
            activity_rows.append(
                dict(
                    run_id=run_id,
                    pool=pool_id,
                    level=spec["name"],
                    scenario=scenario,
                    accepted_decisions=len(accepted),
                    guard_blocks=sum(d["status"] == "SelfTradeRisk" for d in decisions),
                    bundled_decisions=sum(
                        d.get("bundle_flag", False) for d in accepted
                    ),
                    organic_cap_decisions=sum(
                        d.get("constrained_by") == "organic_volume_cap"
                        for d in accepted
                    ),
                    inventory_recovery_decisions=sum(
                        "inventory" in d.get("direction_reason", "") for d in accepted
                    ),
                    mean_interval_seconds=sum(
                        d["recommended_interval_sec"] for d in accepted
                    )
                    / len(accepted)
                    if accepted
                    else None,
                )
            )
            decision_bytes = gzip.compress(
                json.dumps(decisions, separators=(",", ":")).encode(), mtime=0
            )
            budget.reserve(len(decision_bytes) + 65536)
            (directory / "decisions.json.gz").write_bytes(decision_bytes)
            (directory / "parameters.json").write_text(
                json.dumps(dict(spec=spec, config=current, seed=seed), indent=2) + "\n"
            )
            organic_count_frames.append(alarm_counts(fired))
            if scenario == "primary":
                window_frames.append(
                    fired[
                        (~fired.robust) & fired.indicator.isin(["a", "c_sliding"])
                    ].copy()
                )
            budget.check(disk=True)
            for risk_weight in current["lambda_sensitivity"]:
                risk_cfg = dict(current, **{"lambda": risk_weight})
                costs = measure(trades, marks, risk_cfg)
                metadata = dict(
                    run_id=run_id,
                    pool=pool_id,
                    level=spec["name"],
                    seed=seed,
                    scenario=scenario,
                    volatility=volatility,
                    **{"lambda": risk_weight},
                )
                summary_frames.append(summarize(fired, costs, metadata))
    return dict(
        baseline_counts=baseline_counts,
        summary_frames=summary_frames,
        organic_count_frames=organic_count_frames,
        window_frames=window_frames,
        activity_rows=activity_rows,
    )


def _run(config, output, sensitivities, budget, planned, jobs=1):
    """Orchestrate calibration, per-pool strategy runs, and report generation.

    Args:
        config: Path to the experiment config directory.
        output: Output directory (created and verified empty by the caller).
        sensitivities: Whether sensitivity scenarios are included.
        budget: Active :class:`Budget` context.
        planned: Admission plan from :func:`resources.plan`.
        jobs: Number of worker processes (1 = serial).

    Returns:
        The consolidated summary DataFrame.
    """
    started = time.monotonic()
    config, output = Path(config), Path(output)
    verify_frozen(config)
    cfg, ladder, pool_config = load_config(config)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be empty to prevent mixing experiments")
    output.mkdir(parents=True, exist_ok=True)
    histories = {p["id"]: config / p["history"] for p in pool_config["pools"]}
    data_kinds = {
        json.loads((path / "meta.json").read_text())["source"]
        for path in histories.values()
    }
    (output / "plan.json").write_text(json.dumps(planned, indent=2) + "\n")
    if len(data_kinds) != 1:
        raise ValueError("Synthetic and historical evidence must not be mixed")
    synthetic = data_kinds == {"synthetic"}
    if not synthetic and pool_config["selection"]["status"] != "frozen":
        raise ValueError("Historical pool selection must be marked frozen")
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unavailable"
    provenance = dict(
        data_kind=next(iter(data_kinds)),
        git_commit=revision,
        python=platform.python_version(),
        protocol_sha256=sha256(config / "protocol.lock.json"),
        m7="S1 inventory-band sweep from M5 at 10%",
        synthetic_warning="Synthetic validation only; not paper evidence"
        if synthetic
        else None,
    )
    provenance["dependencies"] = {
        name: version(name)
        for name in [
            "numpy",
            "pandas",
            "pyarrow",
            "matplotlib",
            "pydantic",
            "httpx",
            "eth-abi",
            "eth-utils",
            "PyYAML",
            "Jinja2",
        ]
    }
    provenance["machine"] = platform.machine()
    provenance["source_sha256"] = {
        str(p.relative_to(ROOT)): sha256(p) for p in sorted(ROOT.rglob("*.py"))
    }
    for name in ("frozen.yaml", "ladder.yaml", "pools.yaml"):
        (output / name).write_bytes((config / name).read_bytes())
    (output / "manifest.json").write_text(json.dumps(provenance, indent=2) + "\n")
    (output / "protocol.lock.json").write_bytes(
        (config / "protocol.lock.json").read_bytes()
    )
    scenarios = [("primary", {})]
    if sensitivities:
        scenarios += [
            ("no_arbitrage", {"arbitrageur": dict(enabled=False, band_bps=30)}),
            ("fee_10", {"fee_bps": 10}),
            ("fee_30", {"fee_bps": 30}),
            (
                "window_half",
                {
                    "window_hours": cfg["window_hours"] / 2,
                    "w_c_hours": cfg["w_c_hours"] / 2,
                },
            ),
            ("depth_half", {"depth_multiplier": 0.5}),
            ("depth_double", {"depth_multiplier": 2.0}),
        ]
    all_summaries = []
    all_windows = []
    all_thresholds = []
    organic_baselines = []
    organic_counts = []
    activity = []
    calibration_pools = [p for p in pool_config["pools"] if p["split"] == "calibration"]
    test_pools = [p for p in pool_config["pools"] if p["split"] == "test"]
    executor = ProcessPoolExecutor(max_workers=jobs) if jobs > 1 else None
    try:
        for scenario, overrides in scenarios:
            current = dict(cfg, **overrides)

            def calibration_job(p, current=current):
                return dict(
                    history=histories[p["id"]],
                    pool_id=p["id"],
                    current=current,
                    ladder=ladder,
                    limits=planned["limits"],
                    started=started,
                    output=output,
                )

            if executor is not None:
                calibration = list(
                    executor.map(
                        _calibration_pool,
                        [calibration_job(p) for p in calibration_pools],
                    )
                )
            else:
                calibration = [
                    _calibration_pool(calibration_job(p)) for p in calibration_pools
                ]
            budget.check(rows=sum(len(frame) for frame in calibration))
            thresholds = calibrate(
                pd.concat(calibration, ignore_index=True), current["threshold_pcts"]
            )
            thresholds["scenario"] = scenario
            all_thresholds.append(thresholds)
            write_frame(
                pd.concat(calibration, ignore_index=True),
                output / f"calibration_{scenario}.parquet",
                budget=budget,
            )

            def test_job(p, current=current, thresholds=thresholds, scenario=scenario):
                return dict(
                    history=histories[p["id"]],
                    pool_id=p["id"],
                    current=current,
                    ladder=ladder,
                    thresholds=thresholds,
                    output=output,
                    scenario=scenario,
                    limits=planned["limits"],
                    started=started,
                )

            if executor is not None:
                results = list(
                    executor.map(_test_pool_job, [test_job(p) for p in test_pools])
                )
            else:
                results = [_test_pool_job(test_job(p)) for p in test_pools]
            for result in results:
                organic_baselines.append(result["baseline_counts"])
                all_windows.extend(result["window_frames"])
                organic_counts.extend(result["organic_count_frames"])
                activity.extend(result["activity_rows"])
                all_summaries.extend(result["summary_frames"])
            print(f"Completed {scenario}", flush=True)
            budget.check(disk=True)
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
    summary = pd.concat(all_summaries, ignore_index=True)
    windows = pd.concat(all_windows, ignore_index=True)
    thresholds = pd.concat(all_thresholds, ignore_index=True)
    budget.reserve(int(summary.memory_usage(deep=True).sum() * 4) + 16 * 1024**2)
    pd.DataFrame(activity).to_csv(output / "ablation_activity.csv", index=False)
    summary.to_csv(output / "summary.csv", index=False)
    thresholds.to_csv(output / "thresholds.csv", index=False)
    aggregate_counts(organic_baselines).to_csv(
        output / "organic_only_alert_load.csv", index=False
    )
    evasion(summary, cfg).to_csv(output / "evasion.csv", index=False)
    from .analysis.report import report

    report(
        output,
        summary,
        windows,
        thresholds,
        cfg,
        synthetic,
        organic_counts=aggregate_counts(organic_counts),
    )
    from .analysis.readiness import assess

    assess(output, summary, pool_config, cfg, synthetic)
    provenance["resources"] = budget.check(disk=True)
    provenance["elapsed_seconds"] = time.monotonic() - started
    provenance["outputs"] = {
        str(p.relative_to(output)): sha256(p)
        for p in sorted(output.rglob("*"))
        if p.is_file() and p.name != "manifest.json"
    }
    (output / "manifest.json").write_text(json.dumps(provenance, indent=2) + "\n")
    return summary


def demo_config(folder, hours=6):
    """Write a synthetic demo protocol and freeze it.

    Args:
        folder: New directory to create.
        hours: Synthetic replay horizon (at least 2).

    Raises:
        ValueError: If "hours" is less than 2.
    """
    if hours < 2:
        raise ValueError("The demo needs at least two hours for complete risk windows")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    frozen = yaml.safe_load((ROOT / "config/frozen.yaml").read_text())
    frozen.update(
        horizon_hours=hours,
        window_hours=1,
        w_c_hours=1,
        step_hours=0.5,
        risk_window_hours=0.5,
        seeds=[42],
        bootstrap_samples=200,
    )
    (folder / "frozen.yaml").write_text(yaml.safe_dump(frozen))
    (folder / "ladder.yaml").write_bytes((ROOT / "config/ladder.yaml").read_bytes())
    pools = []
    for i in range(3):
        generate(folder / f"history_{i}", i, hours=hours)
        pools.append(
            dict(
                id=f"synthetic_{i}",
                history=f"history_{i}",
                split="calibration" if i == 0 else "test",
            )
        )
    (folder / "pools.yaml").write_text(
        yaml.safe_dump(dict(selection=dict(status="synthetic_demo"), pools=pools))
    )
    freeze(folder)


def main():
    """CLI entry point for the demo, plan, freeze, run, and plot commands."""
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    d = sub.add_parser("demo")
    d.add_argument("--output", default="out/demo")
    d.add_argument("--sensitivities", action="store_true")
    d.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Worker processes for parallel pool execution (default 1 = serial)",
    )
    d.add_argument(
        "--hours",
        type=float,
        default=6,
        help="Synthetic replay hours (at least 2; default 6)",
    )
    planning = sub.add_parser(
        "plan", help="Check run counts and data budgets without executing strategies"
    )
    planning.add_argument("--config", required=True)
    planning.add_argument("--sensitivities", action="store_true")
    f = sub.add_parser("freeze")
    f.add_argument("--config", default=str(ROOT / "config"))
    plotting = sub.add_parser(
        "plot", help="Regenerate figures from a completed experiment without replay"
    )
    plotting.add_argument(
        "--input", required=True, help="Completed experiment output directory"
    )
    plotting.add_argument(
        "--output", required=True, help="New or empty directory for regenerated plots"
    )
    r = sub.add_parser("run")
    r.add_argument("--config", required=True)
    r.add_argument("--output", required=True)
    r.add_argument("--sensitivities", action="store_true")
    r.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Worker processes for parallel pool execution (default 1 = serial)",
    )
    args = p.parse_args()
    if args.command == "plan":
        cfg, ladder, pools = load_config(args.config)
        print(
            json.dumps(
                plan(args.config, cfg, ladder, pools, args.sensitivities), indent=2
            )
        )
    elif args.command == "freeze":
        freeze(args.config)
    elif args.command == "demo":
        config = Path(args.output).with_name(Path(args.output).name + "_inputs")
        demo_config(config, args.hours)
        run(config, args.output, args.sensitivities, args.jobs)
    elif args.command == "plot":
        from .analysis.replot import plot_existing

        result = plot_existing(args.input, args.output)
        print(f"Generated plots and derived tables in {result}")
    else:
        run(args.config, args.output, args.sensitivities, args.jobs)


if __name__ == "__main__":
    main()
