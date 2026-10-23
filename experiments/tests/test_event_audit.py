"""Diagnostics expose liquidity evidence without relaxing admission."""

import json

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from experiments.data.audit_pinax_events import (
    MINT,
    SWAP_TOPIC,
    SYNC_TOPIC,
    audit,
    inspect_events,
)
from experiments.protocol import sha256


def event(index, topic, tx="0x" + "11" * 32):
    return dict(
        block_num=100,
        block_index=index,
        topic0=topic,
        tx_hash=tx,
        address="0x" + "22" * 20,
    )


def test_mint_then_swap_and_end_of_transaction_sync():
    rows = [
        event(1, SYNC_TOPIC),
        event(2, MINT),
        event(3, SYNC_TOPIC),
        event(4, SWAP_TOPIC),
        event(5, SYNC_TOPIC, "0x" + "33" * 32),
    ]
    counts, issues = inspect_events(list(reversed(rows)))
    assert counts["matched"] == 1 and counts["pairing_issues"] == 2
    assert issues[0]["block_index"] == 1 and issues[0]["mint_in_tx"]
    assert not issues[1]["mint_in_tx"]


def test_pairing_does_not_cross_transactions():
    counts, issues = inspect_events(
        [event(1, SYNC_TOPIC), event(2, SWAP_TOPIC, "0x" + "33" * 32)]
    )
    assert counts["matched"] == 0 and len(issues) == 2
    counts, issues = inspect_events(
        [
            event(1, SYNC_TOPIC),
            event(2, SWAP_TOPIC),
            event(3, SYNC_TOPIC),
            event(4, SWAP_TOPIC),
        ]
    )
    assert counts["matched"] == 2 and not issues


def test_audit_filters_lookback_verifies_hash_and_continues(tmp_path):
    config, raw = tmp_path / "config", tmp_path / "raw"
    config.mkdir()
    (raw / "pool_01").mkdir(parents=True)
    rows = [
        event(1, SYNC_TOPIC) | {"block_num": 99},
        event(2, SYNC_TOPIC),
        event(3, SWAP_TOPIC),
    ]
    path = raw / "pool_01/logs.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    pools = [
        dict(id=f"pool_{n:02}", pair=rows[0]["address"], start_block=100, end_block=101)
        for n in (1, 2)
    ]
    (config / "pools.yaml").write_text(yaml.safe_dump(dict(pools=pools)))
    manifest = dict(complete=True, outputs={"pool_01/logs.parquet": sha256(path)})
    (raw / "acquisition.json").write_text(json.dumps(manifest))
    result = audit(config, raw, tmp_path / "audit")
    assert result.status.tolist() == ["no_pairing_issue_detected", "audit_error"]
    assert result.iloc[0]["matched"] == 1
    manifest["outputs"]["pool_01/logs.parquet"] = "changed"
    (raw / "acquisition.json").write_text(json.dumps(manifest))
    result = audit(config, raw, tmp_path / "changed_audit")
    assert result.status.tolist() == ["audit_error", "audit_error"]
