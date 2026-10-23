"""Cohort reproducibility, true factory identity and freeze qualification gates."""

import json

import pandas as pd
import pytest
import yaml

from experiments import ROOT
from experiments.protocol import fingerprint, sha256
from experiments.run import demo_config
from experiments.study import (
    END,
    START,
    WBNB,
    audit_histories,
    pair_address,
    prepare_imports,
    select_cohort,
)


def test_reproducible_declared_cohort(tmp_path):
    discovery = ROOT / "config/study_evidence/discovery.csv"
    selected, _ = select_cohort(discovery)
    shuffled = tmp_path / "shuffled.csv"
    pd.read_csv(discovery).sample(frac=1, random_state=42).to_csv(shuffled, index=False)
    again, _ = select_cohort(shuffled)

    def assignment(rows):
        return {r["pair"]: r["split"] for r in rows}

    assert assignment(selected) == assignment(again)
    declared = yaml.safe_load((ROOT / "config/pools.yaml").read_text())
    assert assignment(selected) == assignment(declared["pools"])
    assert len(selected) == len({r["token"] for r in selected}) == 36
    assert len({r["symbol"].casefold() for r in selected}) == 36
    for stratum in ("low", "medium", "high"):
        group = [r for r in selected if r["discovery_stratum"] == stratum]
        assert sum(r["split"] == "calibration" for r in group) == 4
        assert sum(r["split"] == "test" for r in group) == 8
    assert all(
        r["start_block"] == START and r["end_block"] == END for r in declared["pools"]
    )
    assert END - START + 1 == 574989


def test_factory_derivation_against_known_cake_pair():
    cake = "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82"
    assert pair_address(cake, WBNB) == "0x0ed7e52944161450477ee417de9cd3a859b14fd0"
    assert pair_address(WBNB, cake) == pair_address(cake, WBNB)


def test_missing_histories_cannot_qualify(tmp_path, monkeypatch):
    def missing(_):
        raise FileNotFoundError("No fixture history")

    monkeypatch.setattr("experiments.study.load_history", missing)
    result = audit_histories(ROOT / "config", tmp_path / "audit")
    assert not result["qualified"]
    assert result["failures"] == 36
    assert (tmp_path / "audit/qualification.csv").is_file()


def test_prepare_imports_preserves_metadata_and_budgets(tmp_path):
    output = tmp_path / "imports"
    assert prepare_imports(ROOT / "config", output)["import_configs"] == 36
    cohort = yaml.safe_load((ROOT / "config/pools.yaml").read_text())
    for pool in cohort["pools"]:
        cfg = yaml.safe_load((output / (pool["id"] + ".yaml")).read_text())
        assert cfg["pair"] == pool["pair"]
        assert cfg["quote_index"] == pool["quote_index"]
        assert cfg[f"decimals{1 - pool['quote_index']}"] == pool["token_decimals"]
        assert cfg["start_block"] == START and cfg["end_block"] == END
        assert cfg["resources"]["max_blocks_per_pool"] == 800000
        assert cfg["assume_usdt_usd"] == (pool["quote_symbol"] == "USDT")
    with pytest.raises(ValueError, match="new directory"):
        prepare_imports(ROOT / "config", output)


def test_freeze_binds_qualification_to_exact_inputs(tmp_path):
    folder = tmp_path / "protocol"
    demo_config(folder, hours=2)
    (folder / "protocol.lock.json").unlink()
    original = fingerprint(folder)
    cfg = yaml.safe_load((folder / "pools.yaml").read_text())
    evidence = folder / "discovery.csv"
    evidence.write_text("pair\nfixture\n")
    cfg["selection"].update(
        qualification_report="qualification.json",
        discovery_evidence="discovery.csv",
        discovery_sha256=sha256(evidence),
    )
    (folder / "pools.yaml").write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError, match="qualification report"):
        fingerprint(folder)
    report = dict(
        qualified=True,
        inputs={k: v for k, v in original["inputs"].items() if "/" in k},
        config_sha256=sha256(folder / "pools.yaml"),
    )
    (folder / "qualification.json").write_text(json.dumps(report))
    assert "qualification.json" in fingerprint(folder)["inputs"]
    report["qualified"] = False
    (folder / "qualification.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="qualification failed"):
        fingerprint(folder)
    report["qualified"] = True
    report["inputs"]["synthetic_0/meta.json"] = "changed"
    (folder / "qualification.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="inputs changed"):
        fingerprint(folder)
