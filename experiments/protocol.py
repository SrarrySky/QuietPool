"""Freeze inputs before testing and record reproducible provenance."""

import hashlib
import json
import re
from pathlib import Path

import yaml

from . import ROOT


def sha256(path):
    """Return the SHA-256 hex digest of a file, read in bounded chunks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_vendor():
    """Verify the frozen vendor subset against its manifest.

    Returns:
        The parsed vendor manifest.

    Raises:
        ValueError: If any vendored file no longer matches its recorded hash.
    """
    manifest = json.loads((ROOT / "vendor/manifest.json").read_text())
    for relative, expected in manifest["files"].items():
        path = ROOT / "vendor" / relative
        raw = path.read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if sha256(path) != expected or blob != manifest["git_blobs"][relative]:
            raise ValueError(f"Vendor file changed: {relative}")
    return manifest


def load_config(folder):
    """Load and validate the frozen, ladder, and pool configuration files.

    Args:
        folder: Directory containing "frozen.yaml", "ladder.yaml", and
            "pools.yaml".

    Returns:
        Tuple "(frozen, ladder, pools)" of decoded mappings.

    Raises:
        ValueError: If any preregistered constraint is violated.
    """
    folder = Path(folder)
    frozen = yaml.safe_load((folder / "frozen.yaml").read_text())
    ladder = yaml.safe_load((folder / "ladder.yaml").read_text())
    pools = yaml.safe_load((folder / "pools.yaml").read_text())
    if not pools["pools"]:
        raise ValueError(
            "No pools specified. Use demo for synthetic validation or freeze a real pool list."
        )
    if len({p["id"] for p in pools["pools"]}) != len(pools["pools"]):
        raise ValueError("Pool IDs must be unique")
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+", p["id"]) for p in pools["pools"]):
        raise ValueError("Pool IDs must be safe single directory names")
    if not all(p["split"] in {"calibration", "test"} for p in pools["pools"]):
        raise ValueError("Unknown data split")
    if set(p["split"] for p in pools["pools"]) != {"calibration", "test"}:
        raise ValueError("Separate calibration and test pools are required")
    for key in (
        "horizon_hours",
        "window_hours",
        "step_hours",
        "w_c_hours",
        "risk_window_hours",
    ):
        if frozen[key] <= 0 or frozen[key] * 3600 != int(frozen[key] * 3600):
            raise ValueError(f"Invalid {key}")
    if frozen["horizon_hours"] < max(
        frozen["window_hours"], frozen["w_c_hours"], 2 * frozen["risk_window_hours"]
    ):
        raise ValueError("Horizon must contain complete evaluation and risk windows")
    if not 0 <= frozen["epsilon"] <= 1 or frozen["V_star_usd"] <= 0:
        raise ValueError("Invalid target or epsilon")
    if not frozen["seeds"] or len(set(frozen["seeds"])) != len(frozen["seeds"]):
        raise ValueError("Seeds must be nonempty and unique")
    if any(not isinstance(seed, int) or seed < 0 for seed in frozen["seeds"]):
        raise ValueError("Seeds must be nonnegative integers")
    if sorted(frozen["threshold_pcts"]) != [95, 99]:
        raise ValueError(
            "Both preregistered threshold percentiles 95 and 99 are required"
        )
    if not 0 <= frozen["fee_bps"] < 10000 or not 0 < frozen["c_min_share"] < 1:
        raise ValueError("Invalid fee or minimum share")
    if (
        any(x < 0 for x in frozen["lambda_sensitivity"])
        or frozen["lambda"] not in frozen["lambda_sensitivity"]
    ):
        raise ValueError(
            "Risk sensitivity weights must include the primary nonnegative weight"
        )
    if frozen["initial_quote_usd"] < 0 or frozen["initial_token_usd"] < 0:
        raise ValueError("Initial endowments cannot be negative")
    if not 1 <= ladder["wallets"] <= 5:
        raise ValueError("Invalid wallets")
    if "specs" in ladder:
        specs = ladder["specs"]
        if not specs or any(not s.get("name") for s in specs):
            raise ValueError("Strategy specs must be nonempty and named")
        if len({s["name"] for s in specs}) != len(specs):
            raise ValueError("Strategy spec names must be unique")
        for s in specs:
            band = s.get("band")
            if band is not None and (
                not isinstance(band, (list, tuple))
                or len(band) != 2
                or not 0 < band[0] < band[1]
            ):
                raise ValueError(f"Invalid inventory band in spec {s['name']}")
            if not 1 <= s.get("available_wallet_count", 1) <= ladder["wallets"]:
                raise ValueError(f"Invalid available_wallet_count in spec {s['name']}")
            if not (
                1
                <= s.get("min_interval_sec", 10)
                <= s.get("base_interval_sec", 30)
                <= s.get("max_interval_sec", 120)
                <= 7200
            ):
                raise ValueError(f"Invalid interval chain in spec {s['name']}")
    else:
        if any(not 0 < soft < hard for soft, hard in ladder["inventory_bands"]):
            raise ValueError("Invalid inventory bands")
        if ladder.get("m7_definition") != "S1_inventory_band_sweep_from_M5":
            raise ValueError("M7 mapping must be explicit")
    return frozen, ladder, pools


def fingerprint(folder):
    """Hash every input that determines an experiment's outcome.

    Args:
        folder: Config directory.

    Returns:
        Dict with "inputs" (file hashes) and "vendor" (vendor manifest).
    """
    folder = Path(folder)
    cfg, ladder, pools = load_config(folder)
    inputs = {
        name: sha256(folder / name)
        for name in ("frozen.yaml", "ladder.yaml", "pools.yaml")
    }
    pair_splits = {}
    for pool in pools["pools"]:
        path = (folder / pool["history"]).resolve()
        meta = json.loads((path / "meta.json").read_text())
        if (path / "INCOMPLETE.json").exists() or (
            meta.get("provider") == "Pinax"
            and not (path / "import_manifest.json").is_file()
        ):
            raise ValueError("Incomplete history or missing Pinax import manifest")
        pair = meta["pair"].lower()
        if pool.get("pair") and pair != pool["pair"].lower():
            raise ValueError("History pair does not match the declared cohort")
        if pair in pair_splits:
            raise ValueError(
                "Each pair must appear once; duplicated pairs are not independent pool clusters"
            )
        pair_splits[pair] = pool["split"]
        for filename in ("meta.json", "swaps.parquet", "blocks.parquet"):
            inputs[pool["id"] + "/" + filename] = sha256(path / filename)
        if (path / "import_manifest.json").exists():
            inputs[pool["id"] + "/import_manifest.json"] = sha256(
                path / "import_manifest.json"
            )
    selection = pools.get("selection", {})
    if selection.get("amendment_manifest"):
        amendment_path = folder / selection["amendment_manifest"]
        amendment = json.loads(amendment_path.read_text())
        for name, digest in amendment.get("evidence_files", {}).items():
            if sha256(amendment_path.parent / name) != digest:
                raise ValueError("Amendment evidence changed")
        inputs["amendment_manifest.json"] = sha256(amendment_path)
    if selection.get("qualification_report"):
        report_path = folder / selection["qualification_report"]
        if not report_path.is_file():
            raise ValueError(
                "Historical cohort requires a successful baseline qualification report"
            )
        report = json.loads(report_path.read_text())
        historical_inputs = {key: value for key, value in inputs.items() if "/" in key}
        if (
            report.get("qualified") is not True
            or report.get("inputs") != historical_inputs
            or report.get("config_sha256") != sha256(folder / "pools.yaml")
        ):
            raise ValueError("Cohort qualification failed or its inputs changed")
        evidence = folder / selection["discovery_evidence"]
        if sha256(evidence) != selection["discovery_sha256"]:
            raise ValueError("Discovery evidence changed")
        inputs["qualification.json"] = sha256(report_path)
        inputs["discovery.csv"] = sha256(evidence)
    return dict(inputs=inputs, vendor=verify_vendor())


def freeze(folder):
    """Write "protocol.lock.json", refusing to overwrite an existing protocol.

    Args:
        folder: Config directory.

    Returns:
        The fingerprint record that was written.

    Raises:
        ValueError: If a protocol lock already exists.
    """
    folder = Path(folder)
    path = folder / "protocol.lock.json"
    if path.exists():
        raise ValueError(
            "Protocol already frozen; use a new directory for a new protocol"
        )
    record = fingerprint(folder)
    path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
    return record


def verify_frozen(folder):
    """Verify the existing protocol lock still matches current inputs.

    Args:
        folder: Config directory.

    Raises:
        ValueError: If the lock is missing or the inputs have changed.
    """
    path = Path(folder) / "protocol.lock.json"
    if not path.exists() or json.loads(path.read_text()) != fingerprint(folder):
        raise ValueError("Protocol or input data changed; create a new frozen protocol")
