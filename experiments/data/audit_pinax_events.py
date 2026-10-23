"""Diagnose unsupported pool events offline without changing study eligibility.

Produces diagnostic counts and issue listings only; it certifies neither
eligibility nor reserve-transition validity.
"""

import argparse
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import yaml
from eth_utils import keccak

from .. import ROOT, pinax_date
from ..protocol import sha256
from ..resources import Budget
from .fetch_pool_history import SWAP_TOPIC, SYNC_TOPIC
from .import_pinax import hex_value

MINT = "0x" + keccak(text="Mint(address,uint256,uint256)").hex()
BURN = "0x" + keccak(text="Burn(address,uint256,uint256,address)").hex()
ISSUE_COLUMNS = [
    "pool",
    "block",
    "block_index",
    "tx_hash",
    "reason",
    "mint_in_tx",
    "burn_in_tx",
]


def inspect_events(events):
    """Return Sync/Swap pairing issues and same-transaction liquidity evidence.

    Args:
        events: Pool logs with "block_num", "block_index", "tx_hash", and
            "topic0" fields.

    Returns:
        Tuple of a counts dict and a list of pairing-issue dicts.
    """
    events = sorted(events, key=lambda r: (r["block_num"], r["block_index"]))
    evidence = {}
    for event in events:
        key = (event["block_num"], event["tx_hash"])
        evidence.setdefault(key, set()).add(event["topic0"])
    pending, issues, matched = {}, [], 0

    def issue(event, reason):
        topics = evidence[(event["block_num"], event["tx_hash"])]
        issues.append(
            dict(
                block=event["block_num"],
                block_index=event["block_index"],
                tx_hash=event["tx_hash"],
                reason=reason,
                mint_in_tx=MINT in topics,
                burn_in_tx=BURN in topics,
            )
        )

    for event in events:
        key = (event["block_num"], event["tx_hash"])
        if event["topic0"] == SYNC_TOPIC:
            if key in pending:
                issue(pending[key], "Sync superseded before Swap")
            pending[key] = event
        elif event["topic0"] == SWAP_TOPIC:
            if key not in pending:
                issue(event, "Swap without preceding Sync")
            else:
                pending.pop(key)
                matched += 1
    for event in pending.values():
        issue(event, "Sync without subsequent Swap")
    counts = {
        name: sum(e["topic0"] == topic for e in events)
        for name, topic in dict(
            sync=SYNC_TOPIC, swap=SWAP_TOPIC, mint=MINT, burn=BURN
        ).items()
    }
    return counts | dict(matched=matched, pairing_issues=len(issues)), issues


def audit(config, raw, output):
    """Audit every pool's raw logs for unsupported events without any changes.

    Verifies each "logs.parquet" against the acquisition hashes, inspects
    Sync/Swap pairing plus same-transaction Mint/Burn evidence, and writes the
    summary and issue reports.

    Args:
        config: Study config directory containing "pools.yaml".
        raw: Raw acquisition directory with "acquisition.json" and pool logs.
        output: New directory for "event_summary.csv" and "event_issues.csv".

    Returns:
        A :class:`pandas.DataFrame` of per-pool audit statuses.
    """
    config, raw, output = Path(config), Path(raw), Path(output)
    if output.exists():
        raise ValueError("Audit output must be a new directory")
    pools = yaml.safe_load((config / "pools.yaml").read_text())["pools"]
    acquisition = json.loads((raw / "acquisition.json").read_text())
    if acquisition.get("complete") is not True:
        raise ValueError("Acquisition is incomplete")
    output.mkdir(parents=True)
    summaries, details = [], []
    with Budget(dict(memory_mb=4096, disk_mb=128, wall_seconds=1800), output) as budget:
        for pool in pools:
            relative = f"{pool['id']}/logs.parquet"
            path = raw / relative
            try:
                if sha256(path) != acquisition["outputs"].get(relative):
                    raise ValueError("Raw logs differ from acquisition hash")
                source = pq.ParquetFile(path)
                if source.metadata.num_rows > 2_000_000:
                    raise ValueError("Raw pool logs exceed two-million-row audit cap")
                events = []
                for batch in source.iter_batches(
                    batch_size=8192,
                    columns=[
                        "block_num",
                        "block_index",
                        "tx_hash",
                        "address",
                        "topic0",
                    ],
                ):
                    budget.check()
                    for row in batch.to_pylist():
                        if (
                            not pool["start_block"]
                            <= row["block_num"]
                            <= pool["end_block"]
                            or hex_value(row["address"], 20) != pool["pair"]
                        ):
                            continue
                        topic = (
                            hex_value(row["topic0"], 32)
                            if row["topic0"] is not None
                            else None
                        )
                        if topic in (SYNC_TOPIC, SWAP_TOPIC, MINT, BURN):
                            row["topic0"] = topic
                            row["tx_hash"] = hex_value(row["tx_hash"], 32)
                            events.append(row)
                counts, issues = inspect_events(events)
                status = (
                    "liquidity_events_require_import"
                    if counts["mint"] or counts["burn"]
                    else "unsupported_events"
                    if counts["pairing_issues"]
                    else "no_pairing_issue_detected"
                )
                summaries.append(dict(pool=pool["id"], status=status, **counts))
                details.extend(dict(pool=pool["id"], **issue) for issue in issues)
                print(f"{pool['id']}: {status}; {counts}", flush=True)
            except (ValueError, KeyError, OSError) as error:
                summaries.append(
                    dict(pool=pool["id"], status="audit_error", error=str(error))
                )
                print(f"{pool['id']}: audit_error: {error}", flush=True)
        summary = pd.DataFrame(summaries)
        detail = pd.DataFrame(details, columns=ISSUE_COLUMNS)
        budget.reserve(
            int(
                summary.memory_usage(deep=True).sum()
                + detail.memory_usage(deep=True).sum()
            )
            * 3
            + 4096
        )
        summary.to_csv(output / "event_summary.csv", index=False)
        detail.to_csv(output / "event_issues.csv", index=False)
        (output / "audit.json").write_text(
            json.dumps(
                dict(
                    cohort_sha256=sha256(config / "pools.yaml"),
                    acquisition_sha256=sha256(raw / "acquisition.json"),
                    note="Diagnostic only. No eligibility, reserve transitions, chain integrity, or model validity is certified. Mint/Burn flags indicate same-transaction evidence, not proof of cause. No windows or pools were changed.",
                    status_counts=summary.status.value_counts().to_dict(),
                ),
                indent=2,
            )
            + "\n"
        )
    return summary


def main():
    """Parse arguments and run the offline event audit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ROOT / "config"))
    parser.add_argument("--raw", default=f"data/pinax_{pinax_date()}/raw")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    audit(args.config, args.raw, args.output)


if __name__ == "__main__":
    main()
