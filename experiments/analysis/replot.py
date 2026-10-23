"""Regenerate paper figures from a completed experiment without replaying it.

Reads the immutable output inventory of a finished run and re-invokes the
report writer into a separate, provenance-bound directory.
"""

import json
from pathlib import Path

import pandas as pd
import yaml

from ..protocol import sha256
from .report import report

WINDOW_COLUMNS = [
    "actor",
    "indicator",
    "robust",
    "scenario",
    "view",
    "entity_volume",
    "organic_volume",
    "threshold",
    "value",
    "fired",
    "run_id",
    "threshold_pct",
    "pool",
    "level",
    "evaluable",
]

COUNT_KEYS = ["pool", "view", "threshold_pct", "robust", "scenario", "indicator"]


def _require_files(folder, names):
    """Raise if any of "names" is not a file directly under "folder".

    Args:
        folder (pathlib.Path): Directory to inspect.
        names (list[str]): Relative filenames that must exist.

    Raises:
        ValueError: If one or more required files are missing.
    """
    missing = [name for name in names if not (folder / name).is_file()]
    if missing:
        raise ValueError("Completed experiment is missing: " + ", ".join(missing))


def _window_inputs(source, run_ids):
    """Load primary-scenario window frames and organic alert-load counts.

    Args:
        source (pathlib.Path): Resolved experiment directory containing one
            subdirectory per run.
        run_ids (list[str]): Run identifiers to read.

    Returns:
        tuple[pandas.DataFrame, pandas.DataFrame]: The concatenated primary
        "a"/"c_sliding" windows and the aggregated organic alert-load and
        coverage counts.

    Raises:
        ValueError: If a run directory escapes "source", lacks
            "indicator_windows.parquet", or no primary "a"/"c_sliding"
            windows are available.
    """
    window_frames = []
    count_frames = []
    for run_id in run_ids:
        run_folder = (source / run_id).resolve()
        # Reject run ids that resolve outside the experiment directory.
        if run_folder.parent != source:
            raise ValueError(
                f"Invalid run_id outside the experiment directory: {run_id}"
            )
        path = run_folder / "indicator_windows.parquet"
        if not path.is_file():
            raise ValueError(f"Missing indicator windows for run {run_id}")
        frame = pd.read_parquet(path, columns=WINDOW_COLUMNS)
        organic = frame[frame.actor == "organic"]
        count_frames.append(
            organic.groupby(COUNT_KEYS)
            .agg(
                n=("fired", "size"),
                alarms=("fired", "sum"),
                evaluated=("evaluable", "sum"),
            )
            .reset_index()
        )
        selected = frame[
            (~frame.robust)
            & (frame.scenario == "primary")
            & frame.indicator.isin(["a", "c_sliding"])
        ]
        if not selected.empty:
            window_frames.append(selected)
    if not window_frames:
        raise ValueError("No primary a/c_sliding windows are available for plotting")
    counts = (
        pd.concat(count_frames, ignore_index=True)
        .groupby(COUNT_KEYS)[["n", "alarms", "evaluated"]]
        .sum()
        .reset_index()
    )
    counts["alert_load"] = counts.alarms / counts.n
    counts["coverage"] = counts.evaluated / counts.n
    return pd.concat(window_frames, ignore_index=True), counts


def plot_existing(source, output):
    """Regenerate figures and derived tables into a new, separate directory.

    Args:
        source (pathlib.Path | str): Completed experiment directory to read.
        output (pathlib.Path | str): New or empty directory to write.

    Returns:
        pathlib.Path: The resolved output directory containing the regenerated
        artifacts and "plot_manifest.json".

    Raises:
        ValueError: If the source is missing, incomplete, equals the output,
            lacks required files or an output inventory, or the output
            directory is non-empty.
    """
    source = Path(source).resolve()
    output = Path(output).resolve()
    if source == output:
        raise ValueError("Plot output must differ from the source experiment directory")
    if not source.is_dir():
        raise ValueError(f"Experiment directory does not exist: {source}")
    if (source / "INCOMPLETE.json").exists():
        raise ValueError("Cannot plot an experiment marked INCOMPLETE")
    _require_files(
        source,
        [
            "summary.csv",
            "thresholds.csv",
            "evasion.csv",
            "frozen.yaml",
            "manifest.json",
            "protocol.lock.json",
        ],
    )
    manifest = json.loads((source / "manifest.json").read_text())
    if "outputs" not in manifest:
        raise ValueError("Experiment manifest has no final output inventory")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Plot output directory must be empty")
    output.mkdir(parents=True, exist_ok=True)

    cfg = yaml.safe_load((source / "frozen.yaml").read_text())
    summary = pd.read_csv(source / "summary.csv")
    thresholds = pd.read_csv(source / "thresholds.csv")
    if summary.empty or "run_id" not in summary:
        raise ValueError("summary.csv has no experiment run identifiers")
    run_ids = sorted(summary.run_id.dropna().astype(str).unique())
    windows, organic_counts = _window_inputs(source, run_ids)
    synthetic = manifest.get("data_kind") == "synthetic"

    report(
        output,
        summary,
        windows,
        thresholds,
        cfg,
        synthetic,
        organic_counts=organic_counts,
        data_root=source,
    )
    generated = {
        str(path.relative_to(output)): sha256(path)
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "plot_manifest.json"
    }
    plot_manifest = {
        "source_experiment": str(source),
        "source_manifest_sha256": sha256(source / "manifest.json"),
        "source_protocol_sha256": sha256(source / "protocol.lock.json"),
        "source_inputs": {
            name: sha256(source / name)
            for name in ("summary.csv", "thresholds.csv", "evasion.csv", "frozen.yaml")
        },
        "synthetic": synthetic,
        "run_count": len(run_ids),
        "outputs": generated,
    }
    (output / "plot_manifest.json").write_text(
        json.dumps(plot_manifest, indent=2) + "\n"
    )
    return output
