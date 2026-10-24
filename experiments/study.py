"""Reproduce the declared retrospective cohort without strategy outcomes."""

import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from eth_utils import keccak

from .data.history import load_history
from .engine import realized_volatility
from .protocol import sha256
from .resources import Budget

FACTORY = "ca143ce32fe78f1f7019d7d551a6402fc5350c73"
INIT_HASH = "00fb7f630766e6a796048ea87d01acd3068e8ff67d078148a3fa3f4a84f69bd5"
USDT = "0x55d398326f99059ff775485246999027b3197955"
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
SEED = "42"
START = 126919958
END = 127494946  # First midnight block is a guard, excluded by the engine.
START_TS = 1791676800
END_TS = START_TS + 72 * 3600


def pair_address(token, quote):
    """Return the CREATE2 pair address for a PancakeSwap V2 token/quote pair."""
    a, b = sorted([token.lower(), quote.lower()])
    salt = keccak(bytes.fromhex(a[2:] + b[2:]))
    return (
        "0x"
        + keccak(b"\xff" + bytes.fromhex(FACTORY) + salt + bytes.fromhex(INIT_HASH))[
            12:
        ].hex()
    )


def rank(pair, purpose):
    """Return a deterministic SHA-256 rank for sampling/splitting a pair."""
    return hashlib.sha256(f"{SEED}|{purpose}|{pair}".encode()).hexdigest()


def select_cohort(discovery):
    """Select the 36-pool cohort from discovery candidates, with an audit trail.

    Discovery-time activity strata are NOT historical volatility strata; strata
    are assigned here only after sorting, and final volatility strata are
    computed later from qualification outputs.

    Args:
        discovery: Path to the discovery CSV.

    Returns:
        Tuple "(selected, audit)" of candidate dict lists.

    Raises:
        ValueError: If fewer than 36 discovery candidates remain.
    """
    frame = pd.read_csv(discovery, keep_default_na=False)
    if frame.pair.duplicated().any():
        raise ValueError("Duplicate discovery pair")
    accepted, audit = [], []
    for row in frame.to_dict("records"):
        symbol = row["symbol"].upper()
        reason = "eligible_discovery_candidate"
        if row["quote"] not in (USDT, WBNB) or row["pair"] != pair_address(
            row["token"], row["quote"]
        ):
            reason = "not_declared_factory_pair"
        elif row["dex"] != "pancakeswap":
            reason = "provider_venue_mismatch"
        elif any(
            s in symbol for s in ("USD", "EUR", "BNB", "BETH", "ANKRETH")
        ) or symbol in {
            "DAI",
            "UST",
            "FRAX",
            "AMPL",
            "BDO",
            "VAI",
            "XAUT",
            "PAXG",
            "BABYDOGE",
            "FLOKI",
        }:
            reason = "a_priori_pegged_rebasing_or_tax_risk_exclusion"
        elif not row["created_ms"] or int(row["created_ms"]) > 1790985600000:
            reason = "not_verified_existing_by_2026_10_03"
        elif not 1000 <= float(row["discovery_volume_usd"]) <= 10_000_000:
            reason = "discovery_volume_outside_1000_10000000"
        elif float(row["discovery_liquidity_usd"]) < 10_000:
            reason = "discovery_liquidity_below_10000"
        row["discovery_decision"] = reason
        audit.append(row)
        if reason == "eligible_discovery_candidate":
            accepted.append(row)
    # Prefer USDT to reduce external-price acquisition. Collapse wrappers with
    # the same symbol as well as duplicate token addresses before splitting.
    accepted.sort(key=lambda r: (r["quote"] != USDT, r["pair"]))
    unique, tokens, symbols = [], set(), set()
    for row in accepted:
        if row["token"] in tokens or row["symbol"].casefold() in symbols:
            row["discovery_decision"] = "duplicate_asset_or_symbol"
            continue
        unique.append(row)
        tokens.add(row["token"])
        symbols.add(row["symbol"].casefold())
    unique.sort(key=lambda r: (float(r["discovery_volume_usd"]), r["pair"]))
    if len(unique) < 36:
        raise ValueError(
            "Fewer than 36 discovery candidates; do not relax rules silently"
        )
    selected = []
    for stratum, indices in zip(
        ("low", "medium", "high"),
        np.array_split(np.arange(len(unique)), 3),
        strict=True,
    ):
        group = [unique[i] for i in indices]
        group.sort(key=lambda r: rank(r["pair"], "sample"))
        for row in group[12:]:
            row["discovery_decision"] = "unselected_reserve_no_automatic_replacement"
        group = sorted(group[:12], key=lambda r: rank(r["pair"], "split"))
        for i, row in enumerate(group):
            row.update(
                discovery_decision="selected_pending_historical_qualification",
                discovery_stratum=stratum,
                split="calibration" if i % 3 == 0 else "test",
            )
            selected.append(row)
    return selected, audit


def prepare_imports(config, output):
    """Write metadata-filled import configs without downloading data.

    Args:
        config: Path to the config directory with "pools.yaml".
        output: New directory to write the per-pool import YAMLs into.

    Returns:
        Dict with the import-config count and output path.

    Raises:
        ValueError: If "output" already exists.
    """
    config, output = Path(config).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Import-config output must be a new directory")
    cohort = yaml.safe_load((config / "pools.yaml").read_text())
    output.mkdir(parents=True)
    for pool in cohort["pools"]:
        history = (config / pool["history"]).resolve()
        raw = history.parent / "raw"

        def relative(path):
            return os.path.relpath(path, output)

        qi = pool["quote_index"]
        values = dict(
            chain_id=56,
            snapshot="Pinax BSC blocks=13533 logs=13535 transactions=13533; canonical export completeness must be verified",
            metadata_source="Frozen PancakeSwap token-list and CREATE2 discovery; see study_evidence/sources.json",
            acquisition_manifest=relative(raw / "acquisition.json"),
            pair=pool["pair"],
            token0=pool["token0"],
            token1=pool["token1"],
            decimals0=18 if qi == 0 else pool["token_decimals"],
            decimals1=18 if qi == 1 else pool["token_decimals"],
            quote_index=qi,
            coverage_start=126728031,
            start_block=START,
            end_block=END,
            files=dict(
                blocks=[relative(raw / "blocks.parquet")],
                logs=[relative(raw / pool["id"] / "logs.parquet")],
                transactions=[relative(raw / pool["id"] / "transactions.parquet")],
            ),
            assume_usdt_usd=pool["quote_symbol"] == "USDT",
            quote_prices=None
            if pool["quote_symbol"] == "USDT"
            else relative(raw / "bnb_usd.csv"),
            price_max_age_seconds=60,
            max_input_rows=2_000_000,
            max_input_bytes=2 * 1024**3,
            resources=dict(
                memory_mb=4096,
                disk_mb=512,
                wall_seconds=600,
                max_blocks_per_pool=800000,
            ),
        )
        (output / (pool["id"] + ".yaml")).write_text(
            yaml.safe_dump(values, sort_keys=False)
        )
    return dict(import_configs=len(cohort["pools"]), output=str(output))


def qualify_pool(config, pool):
    """Shared historical eligibility rule for original and reserve candidates.

    Args:
        config: Path to the config directory.
        pool: Pool dict from the cohort list.

    Returns:
        Dict with id, split, status, swap count, daily volume, and volatility.

    Raises:
        ValueError: If the pool's history fails any eligibility condition.
    """
    folder = (Path(config) / pool["history"]).resolve()
    meta, swaps, blocks = load_history(folder)
    if meta.get("provider") != "Pinax" or meta["pair"].lower() != pool["pair"]:
        raise ValueError("Expected the declared Pinax pair")
    if (
        blocks.block.iloc[0] != START
        or blocks.block.iloc[-1] != END
        or blocks.ts.iloc[0] != START_TS
        or blocks.ts.iloc[-1] != END_TS
    ):
        raise ValueError("History does not match the declared 72-hour range and guard")
    if (
        meta.get("token0") != pool["token0"]
        or meta.get("token1") != pool["token1"]
        or meta.get("quote_decimals") != pool["quote_decimals"]
        or meta.get("token_decimals") != pool["token_decimals"]
        or meta.get("quote_index") != pool["quote_index"]
        or meta.get("quote_address") != pool[f"token{pool['quote_index']}"]
    ):
        raise ValueError("Token order differs from declared metadata")
    organic = swaps[swaps.ts < END_TS]
    raw_quote = np.where(
        organic.side == "BUY", organic.amount_in_raw, organic.amount_out_raw
    )
    volume = (
        sum(
            float(q) / 10 ** meta["quote_decimals"] * float(p)
            for q, p in zip(raw_quote, organic.quote_usd, strict=True)
        )
        / 3
    )
    volatility = realized_volatility(
        organic,
        [e for e in meta.get("liquidity_events", []) if e["ts"] < END_TS],
    )
    if not np.isfinite(volatility) or volatility <= 0 or len(organic) < 72:
        raise ValueError(
            "Insufficient positive-volatility organic observations (at least 72 swaps)"
        )
    return dict(
        id=pool["id"],
        split=pool["split"],
        status="qualified",
        swaps=len(organic),
        daily_volume_usd=volume,
        volatility=volatility,
    )


def audit_histories(config, output):
    """Qualify every pool under a budget and return the cohort report.

    Args:
        config: Path to the config directory.
        output: New output directory.

    Returns:
        The qualification report dict.
    """
    config = Path(config)
    limits = yaml.safe_load((config / "frozen.yaml").read_text()).get("resources", {})
    with Budget(limits, output) as budget:
        return _audit_histories(config, output, budget)


def _audit_histories(config, output, budget):
    """Fail closed before publishing a qualified cohort; no strategy execution.

    Args:
        config: Path to the config directory.
        output: New output directory.
        budget: Active :class:`Budget` context.

    Returns:
        The qualification report dict.
    """
    config, output = Path(config), Path(output)
    if output.exists():
        raise ValueError("Audit output must be a new directory")
    cfg = yaml.safe_load((config / "pools.yaml").read_text())
    rows, inputs = [], {}
    for pool in cfg["pools"]:
        budget.check()
        folder = (config / pool["history"]).resolve()
        try:
            rows.append(qualify_pool(config, pool))
            for name in (
                "meta.json",
                "swaps.parquet",
                "blocks.parquet",
                "import_manifest.json",
            ):
                inputs[pool["id"] + "/" + name] = sha256(folder / name)
        except (OSError, ValueError, KeyError) as exc:
            rows.append(
                dict(
                    id=pool["id"], split=pool["split"], status="failed", reason=str(exc)
                )
            )
    output.mkdir(parents=True)
    pd.DataFrame(rows).to_csv(output / "qualification.csv", index=False)
    passed = all(r["status"] == "qualified" for r in rows) and len(rows) == len(cfg["pools"])
    report = dict(
        qualified=passed,
        inputs=inputs,
        config_sha256=sha256(config / "pools.yaml"),
        failures=sum(r["status"] != "qualified" for r in rows),
        rule="No replacement or outcome-driven exclusion; amend before running if qualification fails",
    )
    if passed:
        qualified = pd.DataFrame(rows).sort_values(["volatility", "id"])
        # All baseline data are observed before intervention outputs. Report ties
        # and stratum imbalance; do not repair them by moving test identities.
        boundaries = qualified.volatility.quantile([0, 1 / 3, 2 / 3, 1]).to_numpy()
        if len(np.unique(boundaries)) != 4:
            report["qualified"] = False
            report["reason"] = "Volatility quantiles contain tied boundaries"
        else:
            qualified["volatility_stratum"] = pd.qcut(
                qualified.volatility,
                3,
                labels=["low", "medium", "high"],
                duplicates="raise",
            )
            qualified.to_csv(output / "baseline_strata.csv", index=False)
            counts = qualified.groupby(
                ["volatility_stratum", "split"], observed=False
            ).size()
            report["volatility_counts"] = {
                f"{s}/{p}": int(n) for (s, p), n in counts.items()
            }
            if any(
                counts.get((s, "calibration"), 0) < 2 or counts.get((s, "test"), 0) < 4
                for s in ("low", "medium", "high")
            ):
                report["qualified"] = False
                report["reason"] = (
                    "Insufficient calibration/test coverage in baseline volatility strata"
                )
    (output / "qualification.json").write_text(json.dumps(report, indent=2) + "\n")
    budget.check(disk=True)
    return report


def main():
    """CLI entry point for cohort preparation and historical audit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--prepare-imports",
        action="store_true",
        help="Write the 36 import YAMLs without network access",
    )
    args = parser.parse_args()
    result = (
        prepare_imports(args.config, args.output)
        if args.prepare_imports
        else audit_histories(args.config, args.output)
    )
    print(json.dumps({k: v for k, v in result.items() if k != "inputs"}, indent=2))
    if not args.prepare_imports and not result["qualified"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
