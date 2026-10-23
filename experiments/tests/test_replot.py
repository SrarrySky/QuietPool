import json

import pandas as pd
import pytest
import yaml

from experiments.analysis import replot


def completed_result(tmp_path):
    source = tmp_path / "result"
    run = source / "pool_01_M0_42_primary"
    run.mkdir(parents=True)
    pd.DataFrame({"run_id": [run.name]}).to_csv(source / "summary.csv", index=False)
    pd.DataFrame({"threshold": [0.5]}).to_csv(source / "thresholds.csv", index=False)
    pd.DataFrame({"delta_c_bps": [1.0]}).to_csv(source / "evasion.csv", index=False)
    (source / "frozen.yaml").write_text(
        yaml.safe_dump(dict(lambda_sensitivity=[1], threshold_pcts=[95]))
    )
    (source / "protocol.lock.json").write_text("{}\n")
    (source / "manifest.json").write_text(
        json.dumps(dict(data_kind="synthetic", outputs={"summary.csv": "hash"}))
    )
    rows = []
    for actor in ("organic", "manip"):
        for indicator in ("a", "c_sliding"):
            rows.append(
                dict(
                    actor=actor,
                    indicator=indicator,
                    robust=False,
                    scenario="primary",
                    view="V1",
                    entity_volume=1.0,
                    organic_volume=2.0,
                    threshold=0.5,
                    value=0.25,
                    fired=False,
                    run_id=run.name,
                    threshold_pct=95,
                    pool="pool_01",
                    level="M0",
                    evaluable=True,
                )
            )
    pd.DataFrame(rows).to_parquet(run / "indicator_windows.parquet", index=False)
    return source


def test_plot_existing_reconstructs_inputs_and_writes_manifest(tmp_path, monkeypatch):
    source = completed_result(tmp_path)
    output = tmp_path / "plots"
    captured = {}

    def fake_report(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        (args[0] / "figure.txt").write_text("generated\n")

    monkeypatch.setattr(replot, "report", fake_report)
    assert replot.plot_existing(source, output) == output.resolve()
    assert captured["kwargs"]["data_root"] == source.resolve()
    assert set(captured["args"][2].actor) == {"organic", "manip"}
    counts = captured["kwargs"]["organic_counts"]
    assert counts.n.sum() == 2
    manifest = json.loads((output / "plot_manifest.json").read_text())
    assert manifest["synthetic"] is True
    assert manifest["run_count"] == 1
    assert "figure.txt" in manifest["outputs"]


def test_plot_existing_refuses_incomplete_or_in_place_output(tmp_path):
    source = completed_result(tmp_path)
    with pytest.raises(ValueError, match="differ"):
        replot.plot_existing(source, source)
    (source / "INCOMPLETE.json").write_text("{}\n")
    with pytest.raises(ValueError, match="INCOMPLETE"):
        replot.plot_existing(source, tmp_path / "plots")
