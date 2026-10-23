"""Native-schema fixtures test the archive boundary, not historical claims."""

import datetime
import json

import pandas as pd
import pytest
import yaml

from experiments.data.fetch_pool_history import SWAP_TOPIC, SYNC_TOPIC
from experiments.data.history import load_history
from experiments.data.import_pinax import import_history
from experiments.protocol import sha256
from experiments.sandbox.cpmm import CpmmPool


def hx(n, size=32):
    return "0x" + n.to_bytes(size, "big").hex()


def abi(*values):
    return "0x" + "".join(f"{v:064x}" for v in values)


@pytest.fixture
def archive(tmp_path):
    pair = hx(77, 20)
    blocks = [
        dict(
            block_num=n,
            block_id=hx(n),
            parent_id=hx(n - 1),
            lib_num=max(0, n - 1),
            timestamp=datetime.datetime.fromtimestamp(1700000000 + n, datetime.UTC),
        )
        for n in range(1, 5)
    ]
    logs, transactions = [], []
    pool = CpmmPool(10**24, 10**24)
    for n in (1, 2, 3):
        common = dict(block_num=n, block_id=hx(n), timestamp=blocks[n - 1]["timestamp"])
        tx = hx(n + 100)
        event = dict(
            **common, address=pair, tx_hash=tx, tx_index=0, log_index=0, block_index=7
        )
        if n == 1:
            logs.append(dict(event, topic0=SYNC_TOPIC, data=abi(10**24, 10**24)))
            continue
        fill = pool.swap_exact_in(
            side="BUY",
            amount_in_raw=10**18,
            ts=1700000000 + n,
            actor="organic",
            wallet=hx(99, 20),
            quote_usd=1.0,
        )
        logs.append(
            dict(
                event,
                topic0=SYNC_TOPIC,
                data=abi(pool.reserve_quote_raw, pool.reserve_token_raw),
            )
        )
        logs.append(
            dict(
                event | {"block_index": 8, "log_index": 1},
                topic0=SWAP_TOPIC,
                data=abi(10**18, 0, 0, fill.amount_out_raw),
            )
        )
        transactions.append(dict(common, index=0, hash=tx, **{"from": hx(99, 20)}))
    for name, rows in [
        ("blocks", blocks),
        ("logs", logs),
        ("transactions", transactions),
    ]:
        pd.DataFrame(rows).to_parquet(tmp_path / f"{name}.parquet")
    cfg = dict(
        chain_id=56,
        snapshot="synthetic schema fixture; complete",
        metadata_source="fixture",
        pair=pair,
        token0="0x55d398326f99059ff775485246999027b3197955",
        token1=hx(88, 20),
        decimals0=18,
        decimals1=18,
        quote_index=0,
        coverage_start=1,
        start_block=2,
        end_block=3,
        assume_usdt_usd=True,
        files={
            name: [f"{name}.parquet"] for name in ("blocks", "logs", "transactions")
        },
        resources=dict(memory_mb=8192),
    )
    config = tmp_path / "import.yaml"
    config.write_text(yaml.safe_dump(cfg))
    return config, tmp_path / "history"


def test_pinax_roundtrip_and_global_order(archive):
    config, output = archive
    report = import_history(config, output)
    meta, swaps, blocks = load_history(output)
    assert report["anchor_block"] == 1
    assert report["finality_witness"]["lib_num"] == 3
    assert meta["provider"] == "Pinax"
    assert swaps.log_index.tolist() == [8, 8]
    assert swaps.trader.tolist() == [hx(99, 20)] * 2
    assert blocks.block.tolist() == [2, 3]
    with pytest.raises(ValueError, match="new directory"):
        import_history(config, output)


def test_mint_burn_import_persistence_and_exact_accounting(archive):
    from experiments.data.liquidity import BURN_TOPIC, MINT_TOPIC, MODEL

    config, output = archive
    path = config.parent / "logs.parquet"
    logs = pd.read_parquet(path)
    base = logs[(logs.block_num == 2) & (logs.topic0 == SYNC_TOPIC)].iloc[0].to_dict()
    reserves = [int(base["data"][2 + i : 2 + i + 64], 16) for i in (0, 64)]
    q, t = 10**20, 2 * 10**20
    extra = [
        base | dict(block_index=9, data=abi(reserves[0] + q, reserves[1] + t)),
        base | dict(block_index=10, topic0=MINT_TOPIC, data=abi(q, t)),
        base | dict(block_index=11),
        base | dict(block_index=12, topic0=BURN_TOPIC, data=abi(q, t)),
    ]
    pd.concat([logs, pd.DataFrame(extra)], ignore_index=True).to_parquet(path)
    report = import_history(config, output)
    meta, swaps, blocks = load_history(output)
    assert report["liquidity_events"] == 2 and meta["liquidity_model"] == MODEL
    assert len(meta["liquidity_events"]) == 2 and len(swaps) == 2
    assert blocks.block.tolist() == [2, 3]


@pytest.mark.parametrize("problem", [None, "incomplete", "changed"])
def test_acquisition_manifest_gate(archive, problem):
    config, output = archive
    cfg = yaml.safe_load(config.read_text())
    manifest = dict(
        complete=problem != "incomplete",
        outputs={
            name: sha256(config.parent / name)
            for paths in cfg["files"].values()
            for name in paths
        },
    )
    if problem == "changed":
        manifest["outputs"]["logs.parquet"] = "wrong"
    (config.parent / "acquisition.json").write_text(json.dumps(manifest))
    cfg["acquisition_manifest"] = "acquisition.json"
    config.write_text(yaml.safe_dump(cfg))
    if problem:
        with pytest.raises(ValueError, match="incomplete|differs"):
            import_history(config, output)
    else:
        assert import_history(config, output)["anchor_block"] == 1


@pytest.mark.parametrize(
    "problem",
    [
        "parent",
        "gap",
        "finality",
        "sender",
        "duplicate",
        "anchor",
        "hash",
        "sync",
        "budget",
        "price",
    ],
)
def test_pinax_rejects_incomplete_or_invalid_inputs(archive, problem):
    config, output = archive
    table = "blocks" if problem in {"parent", "gap", "finality"} else "logs"
    if problem == "sender":
        table = "transactions"
    path = config.parent / f"{table}.parquet"
    frame = pd.read_parquet(path)
    if problem == "parent":
        frame.loc[2, "parent_id"] = hx(99)
    elif problem == "gap":
        frame = frame.drop(1)
    elif problem == "finality":
        frame["lib_num"] = 0
    elif problem == "sender":
        frame = frame.iloc[:0]
    elif problem == "duplicate":
        frame = pd.concat([frame, frame.iloc[[1]]])
    elif problem == "anchor":
        frame = frame.iloc[1:]
    elif problem == "hash":
        frame.loc[1, "block_id"] = hx(88)
    elif problem == "sync":
        frame = frame.drop(1)
    elif problem in {"budget", "price"}:
        cfg = yaml.safe_load(config.read_text())
        cfg.update(
            {"max_input_rows": 1} if problem == "budget" else {"assume_usdt_usd": False}
        )
        config.write_text(yaml.safe_dump(cfg))
    frame.to_parquet(path)
    with pytest.raises(ValueError):
        import_history(config, output)
    assert not (output / "meta.json").exists()


def test_binary_columns_and_causal_prices(archive):
    config, output = archive
    for table in ("blocks", "logs", "transactions"):
        path = config.parent / f"{table}.parquet"
        frame = pd.read_parquet(path)
        for column in frame:
            if column in {
                "block_id",
                "parent_id",
                "hash",
                "tx_hash",
                "from",
                "address",
                "topic0",
                "data",
            }:
                frame[column] = frame[column].map(lambda x: bytes.fromhex(x[2:]))
        frame.to_parquet(path)
    cfg = yaml.safe_load(config.read_text())
    cfg.update(
        quote_prices="prices.csv", price_max_age_seconds=60, assume_usdt_usd=False
    )
    config.write_text(yaml.safe_dump(cfg))
    pd.DataFrame({"ts": [1700000001, 1700000003], "quote_usd": [2.0, 3.0]}).to_csv(
        config.parent / "prices.csv", index=False
    )
    import_history(config, output)
    _, swaps, blocks = load_history(output)
    assert blocks.quote_usd.tolist() == [2.0, 3.0]
    assert swaps.quote_usd.tolist() == [2.0, 3.0]


@pytest.mark.parametrize("price_ts", [1700000004, 1600000000])
def test_price_future_or_stale_rejected(archive, price_ts):
    config, output = archive
    cfg = yaml.safe_load(config.read_text())
    cfg["quote_prices"] = "prices.csv"
    config.write_text(yaml.safe_dump(cfg))
    pd.DataFrame({"ts": [price_ts], "quote_usd": [1.0]}).to_csv(
        config.parent / "prices.csv", index=False
    )
    with pytest.raises(ValueError, match="USD"):
        import_history(config, output)


def test_protocol_freezes_import_provenance(tmp_path):
    from experiments.protocol import freeze, verify_frozen
    from experiments.run import demo_config

    folder = tmp_path / "protocol"
    demo_config(folder, hours=2)
    (folder / "protocol.lock.json").unlink()
    manifest = folder / "history_0/import_manifest.json"
    manifest.write_text('{"snapshot": "original"}')
    freeze(folder)
    verify_frozen(folder)
    manifest.write_text('{"snapshot": "changed"}')
    with pytest.raises(ValueError, match="changed"):
        verify_frozen(folder)


def test_import_manifest_and_completion_required(archive):
    config, output = archive
    import_history(config, output)
    marker = output / "INCOMPLETE.json"
    marker.write_text("{}")
    with pytest.raises(ValueError, match="Incomplete"):
        load_history(output)
    marker.unlink()
    (output / "import_manifest.json").unlink()
    with pytest.raises(ValueError, match="manifest"):
        load_history(output)
