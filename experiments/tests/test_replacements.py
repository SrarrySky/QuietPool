"""Fixed-order reserve selection, amendment provenance and resumable orchestration."""

import json
import shutil

import pandas as pd
import pytest
import yaml

from experiments import ROOT
from experiments import replacements as r
from experiments.protocol import fingerprint, sha256
from experiments.resources import BudgetExceeded
from experiments.run import demo_config


def test_reserve_order_is_stable_and_disjoint(tmp_path):
    source = ROOT / "config/study_evidence/discovery.csv"
    shuffled = tmp_path / "shuffled.csv"
    pd.read_csv(source).sample(frac=1, random_state=42).to_csv(shuffled, index=False)
    reserves = r.reserve_candidates(source)
    assert reserves == r.reserve_candidates(shuffled)
    original = yaml.safe_load((ROOT / "config/pools.yaml").read_text())["pools"]
    assert len(reserves) == 21
    assert not {p["pair"] for p in reserves} & {p["pair"] for p in original}


def test_first_eligible_and_resource_abort():
    candidates = [dict(id=str(n), pair=str(n)) for n in range(10)]
    checked = []

    def evaluate(c):
        checked.append(c["id"])
        if c["id"] in ("0", "2"):
            raise ValueError("Insufficient observations")
        return dict(status="qualified", swaps=100)

    selected, records = r.choose_replacements(candidates, evaluate)
    assert [c["id"] for c in selected] == ["1", "3", "4", "5", "6"]
    assert checked == [str(n) for n in range(7)]
    assert all(row["status"] == "not_evaluated_quota_filled" for row in records[7:])

    def exhausted(_):
        raise BudgetExceeded("time")

    with pytest.raises(BudgetExceeded):
        r.choose_replacements(candidates, exhausted)


@pytest.mark.parametrize("final_qualified", [True, False])
def test_full_amendment_orchestration_preserves_originals(
    tmp_path, monkeypatch, final_qualified
):
    # Stub chain evaluation/download only; exercise filesystem, candidate ordering,
    # generated import configs, final assignments, hashes and resume end to end.
    source = tmp_path / "source"
    shutil.copytree(ROOT / "config", source)
    cfg = yaml.safe_load((source / "pools.yaml").read_text())
    for pool in cfg["pools"]:
        history = tmp_path / "original" / pool["id"]
        history.mkdir(parents=True)
        for name in (
            "meta.json",
            "swaps.parquet",
            "blocks.parquet",
            "import_manifest.json",
        ):
            (history / name).write_text("fixture")
        pool["history"] = str(history)
    (source / "pools.yaml").write_text(yaml.safe_dump(cfg))
    original_bytes = (source / "pools.yaml").read_bytes()

    def audit(config, output):
        output.mkdir(parents=True)
        rows = [
            dict(
                id=p["id"],
                split=p["split"],
                status="failed" if p["id"] in r.VACANCIES else "qualified",
                reason="Insufficient positive-volatility organic observations (at least 72 swaps)"
                if p["id"] in r.VACANCIES
                else "",
            )
            for p in cfg["pools"]
        ]
        pd.DataFrame(rows).to_csv(output / "qualification.csv", index=False)
        result = dict(qualified=config != source and final_qualified)
        (output / "qualification.json").write_text(json.dumps(result))
        return result

    monkeypatch.setattr(r, "audit_histories", audit)
    monkeypatch.setattr(r, "download", lambda *a, **k: None)
    imported = []

    def import_candidate(config, output):
        imported.append(output.name)
        output.mkdir(parents=True)
        for name in ("meta.json", "swaps.parquet", "blocks.parquet"):
            (output / name).write_text("candidate fixture")
        (output / "import_manifest.json").write_text(
            json.dumps(dict(config_sha256=sha256(config)))
        )

    monkeypatch.setattr(r, "import_history", import_candidate)

    def qualify(config, pool):
        if pool["id"] == "pool_37":
            raise ValueError("Insufficient observations")
        return dict(id=pool["id"], status="qualified", swaps=100, volatility=0.001)

    monkeypatch.setattr(r, "qualify_pool", qualify)
    monkeypatch.setattr(r, "resource_plan", lambda *a, **k: dict(runs=360))
    monkeypatch.setattr(
        r,
        "freeze",
        lambda folder: (folder / "protocol.lock.json").write_text("fixture"),
    )
    monkeypatch.setattr(r, "verify_frozen", lambda folder: None)
    work = tmp_path / "work"
    r.prepare(source, work)
    if not final_qualified:
        with pytest.raises(ValueError, match="final qualification"):
            r.complete(work)
        assert not (work / "final_config/protocol.lock.json").exists()
        assert (source / "pools.yaml").read_bytes() == original_bytes
        return
    final = r.complete(work)
    amended = yaml.safe_load((final / "pools.yaml").read_text())
    assert len(amended["pools"]) == 36
    assert sum(p["split"] == "calibration" for p in amended["pools"]) == 12
    old = {p["id"]: p for p in cfg["pools"]}
    for pool in amended["pools"]:
        assert pool["split"] == old[pool["id"]]["split"]
        assert (pool["pair"] == old[pool["id"]]["pair"]) == (
            pool["id"] not in r.VACANCIES
        )
        if pool["id"] not in r.VACANCIES:
            assert (final / pool["history"]).resolve() == (
                source / old[pool["id"]]["history"]
            ).resolve()
    assert len(imported) == 6
    assert (source / "pools.yaml").read_bytes() == original_bytes
    assert r.prepare(source, work)
    assert r.complete(work) == final and len(imported) == 6
    (work / "reserve_config/pools.yaml").write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        r.verify_prepared(work)


def test_amendment_evidence_is_bound_to_freeze(tmp_path):
    config = tmp_path / "config"
    demo_config(config, hours=2)
    cfg = yaml.safe_load((config / "pools.yaml").read_text())
    cfg["selection"]["amendment_manifest"] = "amendment.json"
    (config / "pools.yaml").write_text(yaml.safe_dump(cfg))
    evidence = config / "screening.json"
    evidence.write_text("[]")
    (config / "amendment.json").write_text(
        json.dumps(dict(evidence_files={"screening.json": sha256(evidence)}))
    )
    assert "amendment_manifest.json" in fingerprint(config)["inputs"]
    evidence.write_text("changed")
    with pytest.raises(ValueError, match="Amendment evidence changed"):
        fingerprint(config)
