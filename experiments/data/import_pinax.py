"""Offline import of a frozen Pinax BSC logs/transactions/blocks export.

Builds a qualified pool history with a contiguous header chain, a pre-start
Sync anchor, per-swap trader attribution, and exact reserve-transition checks.
"""

import argparse
import datetime
import json
import re
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import yaml

from ..protocol import sha256
from ..resources import Budget
from .fetch_pool_history import SWAP_TOPIC, SYNC_TOPIC, words
from .history import save_history, validate_history
from .liquidity import BURN_TOPIC, MINT_TOPIC, MODEL, decode_pool_events


def hex_value(value, size=None):
    """Normalize a hex string or bytes to a lowercase "0x"-prefixed form.

    Args:
        value: Hexadecimal string (with or without "0x") or raw bytes.
        size: Optional expected byte width.

    Returns:
        Lowercase "0x"-prefixed hexadecimal string.

    Raises:
        ValueError: If the value is malformed or has the wrong width.
    """
    if isinstance(value, bytes):
        value = value.hex()
    if not isinstance(value, str):
        raise ValueError("Expected hexadecimal string or binary value")
    value = value.lower().removeprefix("0x")
    if not re.fullmatch(r"(?:[0-9a-f]{2})*", value) or (
        size and len(value) != size * 2
    ):
        raise ValueError("Malformed hex value or incorrect width")
    return "0x" + value


def timestamp(value):
    """Convert a typed Pinax timestamp to whole Unix seconds.

    Args:
        value: A :class:`pandas.Timestamp` or :class:`datetime.datetime`.

    Returns:
        Integer Unix timestamp in seconds.

    Raises:
        ValueError: If the value is not a typed UTC timestamp or is not
            whole-second precision.
    """
    # Pinax uses typed UTC timestamps (ms/us), not unlabelled numeric values.
    if not isinstance(value, (pd.Timestamp, datetime.datetime)):
        raise ValueError("Expected a typed Pinax timestamp")
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None or stamp.value % 1_000_000_000:
        raise ValueError("Expected UTC-aware, whole-second BSC timestamps")
    return int(stamp.timestamp())


def import_history(config, output):
    """Import and qualify one pool history from a frozen Pinax export.

    Verifies acquisition-manifest hashes, admission budgets, header-chain
    continuity, a pre-start Sync anchor, trader attribution, causal prices, and
    exact Swap/Mint/Burn reserve transitions before saving the history.

    Args:
        config: Path to the generated pool import YAML.
        output: New directory for the imported history artifacts.

    Returns:
        The import manifest dict.
    """
    config, output = Path(config).resolve(), Path(output)
    cfg = yaml.safe_load(config.read_text())
    if cfg.get("acquisition_manifest"):
        acquisition_path = (config.parent / cfg["acquisition_manifest"]).resolve()
        acquisition = json.loads(acquisition_path.read_text())
        if acquisition.get("complete") is not True:
            raise ValueError("Archive acquisition is incomplete")
        required = [p for paths in cfg["files"].values() for p in paths]
        if cfg.get("quote_prices"):
            required.append(cfg["quote_prices"])
        for relative in required:
            path = (config.parent / relative).resolve()
            key = str(path.relative_to(acquisition_path.parent))
            if acquisition.get("outputs", {}).get(key) != sha256(path):
                raise ValueError(
                    "Input differs from the completed acquisition manifest"
                )
    if output.exists():
        raise ValueError("Output must be a new directory")
    if (
        cfg["chain_id"] != 56
        or not cfg.get("snapshot")
        or not cfg.get("metadata_source")
    ):
        raise ValueError(
            "BSC snapshot identity and token metadata provenance are required"
        )
    start, end, anchor_start = (
        cfg[k] for k in ("start_block", "end_block", "coverage_start")
    )
    if (
        any(type(n) is not int for n in (anchor_start, start, end))
        or not 0 <= anchor_start < start <= end
    ):
        raise ValueError("Invalid coverage/range; include a pre-start Sync")
    pair = hex_value(cfg["pair"], 20)
    qi = cfg["quote_index"]
    if type(qi) is not int or qi not in (0, 1):
        raise ValueError("quote_index must be 0 or 1")
    tokens = [hex_value(cfg[f"token{i}"], 20) for i in range(2)]
    decimals = [cfg[f"decimals{i}"] for i in range(2)]
    if tokens[0] == tokens[1] or any(
        type(d) is not int or not 0 <= d <= 36 for d in decimals
    ):
        raise ValueError("Invalid token metadata")
    limits = cfg.get("resources", {})
    max_rows = cfg.get("max_input_rows", 2_000_000)
    max_bytes = cfg.get("max_input_bytes", 2 * 1024**3)
    if max_rows <= 0 or max_bytes <= 0:
        raise ValueError("Input budgets must be positive")
    files, inventory = {}, []
    total_bytes = total_rows = 0
    for table in ("blocks", "transactions", "logs"):
        files[table] = [(config.parent / p).resolve() for p in cfg["files"][table]]
        if not files[table] or len(set(files[table])) != len(files[table]):
            raise ValueError("Explicit, unique local Parquet files are required")
        for path in files[table]:
            total_bytes += path.stat().st_size
            total_rows += pq.read_metadata(path).num_rows
    if total_bytes > max_bytes or total_rows > max_rows:
        raise ValueError(
            "Input admission budget exceeded; obtain a smaller provider export"
        )
    with Budget(limits, output) as budget:

        def rows(table):
            columns = {
                "blocks": [
                    "block_num",
                    "block_id",
                    "parent_id",
                    "timestamp",
                    "lib_num",
                ],
                "logs": [
                    "block_num",
                    "block_id",
                    "timestamp",
                    "block_index",
                    "tx_index",
                    "tx_hash",
                    "address",
                    "topic0",
                    "data",
                ],
                "transactions": [
                    "block_num",
                    "block_id",
                    "timestamp",
                    "index",
                    "hash",
                    "from",
                ],
            }[table]
            for path in files[table]:
                budget.check()
                inventory.append(
                    dict(
                        table=table,
                        file=path.name,
                        bytes=path.stat().st_size,
                        sha256=sha256(path),
                    )
                )
                parquet = pq.ParquetFile(path)
                if "fork_step" in parquet.schema_arrow.names:
                    raise ValueError(
                        "Export a canonical snapshot without fork-stream rows"
                    )
                if not set(columns).issubset(parquet.schema_arrow.names):
                    raise ValueError(f"Missing native Pinax columns in {table}")
                for batch in parquet.iter_batches(batch_size=2048, columns=columns):
                    budget.check()
                    # Filter in Arrow before materializing Python dictionaries.
                    mask = pc.greater_equal(batch.column("block_num"), anchor_start)
                    if table != "blocks":
                        mask = pc.and_(
                            mask, pc.less_equal(batch.column("block_num"), end)
                        )
                    batch = batch.filter(mask)
                    if table == "logs":
                        addresses = batch.column("address")
                        if pa.types.is_binary(addresses.type):
                            match = pc.equal(addresses, bytes.fromhex(pair[2:]))
                        else:
                            match = pc.is_in(
                                pc.utf8_lower(addresses),
                                value_set=pa.array([pair, pair[2:]]),
                            )
                        batch = batch.filter(match)
                    yield from batch.to_pylist()

        headers = {}
        for row in rows("blocks"):
            n = int(row["block_num"])
            if n < anchor_start:
                continue
            if n in headers:
                raise ValueError("Duplicate/forked block")
            headers[n] = dict(
                block=n,
                ts=timestamp(row["timestamp"]),
                hash=hex_value(row["block_id"], 32),
                parent_hash=hex_value(row["parent_id"], 32),
                lib_num=int(row["lib_num"]),
            )
            if len(headers) > budget.limits["max_blocks_per_pool"]:
                raise ValueError("Selected headers exceed max_blocks_per_pool")
        ordered = sorted(headers)
        if not ordered or ordered[0] != anchor_start or ordered[-1] < end:
            raise ValueError("Missing block coverage")
        for left, right in zip(ordered, ordered[1:], strict=False):
            if (
                right != left + 1
                or headers[right]["parent_hash"] != headers[left]["hash"]
            ):
                raise ValueError("Missing block or inconsistent parent hash")
            if headers[right]["ts"] < headers[left]["ts"]:
                raise ValueError("Nonmonotonic block timestamps")
        if not end <= headers[ordered[-1]]["lib_num"] <= ordered[-1]:
            raise ValueError(
                "Include a later contiguous block with lib_num >= end_block"
            )

        def check_row(row):
            n = int(row["block_num"])
            if n not in headers or hex_value(row["block_id"], 32) != headers[n]["hash"]:
                raise ValueError("Record does not match canonical block")
            if timestamp(row["timestamp"]) != headers[n]["ts"]:
                raise ValueError("Record timestamp does not match block")
            return n

        events, seen, anchor = [], set(), None
        tx_needed = {}
        for row in rows("logs"):
            n = int(row["block_num"])
            if not anchor_start <= n <= end or hex_value(row["address"], 20) != pair:
                continue
            check_row(row)
            position = (n, int(row["block_index"]))
            if position[1] < 0 or position in seen:
                raise ValueError("Duplicate/invalid block_index")
            seen.add(position)
            if len(seen) > budget.limits["max_swaps_per_pool"] * 4:
                raise ValueError("Selected pool logs exceed event budget")
            topic = hex_value(row["topic0"], 32) if row["topic0"] is not None else None
            data = hex_value(row["data"] or b"")
            if n < start:
                if topic == SYNC_TOPIC and (anchor is None or position > anchor[0]):
                    anchor = (position, words(data))
                continue
            if topic not in (SYNC_TOPIC, SWAP_TOPIC, MINT_TOPIC, BURN_TOPIC):
                # Transfer/Approval do not themselves update pool reserves.
                continue
            tx = hex_value(row["tx_hash"], 32)
            identity = (n, int(row["tx_index"]))
            if tx in tx_needed and tx_needed[tx] != identity:
                raise ValueError("Inconsistent transaction identity")
            tx_needed[tx] = identity
            events.append(
                dict(
                    blockNumber=hex(n),
                    logIndex=hex(position[1]),
                    transactionHash=tx,
                    topics=[topic],
                    data=data,
                )
            )
        if (
            anchor is None
            or len(anchor[1]) != 2
            or any(not 0 < r < 2**112 for r in anchor[1])
        ):
            raise ValueError("Missing or malformed pre-start Sync reserve anchor")
        traders = {}
        identities = set()
        for row in rows("transactions"):
            tx = hex_value(row["hash"], 32)
            if tx not in tx_needed:
                continue
            n = check_row(row)
            identity = (n, int(row["index"]))
            if tx in traders or identity in identities or identity != tx_needed[tx]:
                raise ValueError("Duplicate or mismatched transaction")
            identities.add(identity)
            traders[tx] = hex_value(row["from"], 20)
        if set(traders) != set(tx_needed):
            raise ValueError("Missing transaction.from for selected logs")
        blocks = pd.DataFrame([headers[n] for n in ordered if start <= n <= end])
        meta = dict(
            pair=pair,
            source="historical",
            provider="Pinax",
            chain_id=56,
            token0=tokens[0],
            token1=tokens[1],
            quote_index=qi,
            quote_address=tokens[qi],
            quote_decimals=decimals[qi],
            token_decimals=decimals[1 - qi],
            reserve_quote_raw=str(anchor[1][qi]),
            reserve_token_raw=str(anchor[1][1 - qi]),
        )
        if cfg.get("quote_prices"):
            price_path = config.parent / cfg["quote_prices"]
            if price_path.stat().st_size > 32 * 1024**2:
                raise ValueError("Price CSV exceeds 32 MiB")
            prices = pd.read_csv(price_path).sort_values("ts")
            if prices.ts.duplicated().any() or not (prices.ts % 1 == 0).all():
                raise ValueError("Price timestamps must be unique integer seconds")
            prices["ts"] = prices.ts.astype("int64")
            age = cfg.get("price_max_age_seconds", 60)
            if type(age) is not int or age <= 0:
                raise ValueError("Invalid causal price age")
            blocks = pd.merge_asof(
                blocks,
                prices[["ts", "quote_usd"]],
                on="ts",
                direction="backward",
                tolerance=age,
            )
            meta["price_sha256"] = sha256(price_path)
            meta["usd_assumption"] = f"Offline causal prices; max age {age} seconds"
        elif (
            tokens[qi] == "0x55d398326f99059ff775485246999027b3197955"
            and cfg.get("assume_usdt_usd") is True
        ):
            blocks["quote_usd"] = 1.0
            meta["usd_assumption"] = "USDT = 1 USD (explicit assumption)"
        else:
            raise ValueError(
                "Supply causal offline quote_prices or explicitly assume USDT=USD"
            )
        swaps, liquidity = decode_pool_events(
            events, qi, dict(zip(blocks.block, blocks.ts, strict=True)), traders
        )
        if swaps.empty:
            raise ValueError("No swaps in selected interval")
        if liquidity:
            meta["liquidity_model"] = MODEL
            meta["liquidity_events"] = liquidity
        swaps["quote_usd"] = swaps.block.map(
            dict(zip(blocks.block, blocks.quote_usd, strict=True))
        )
        fidelity = validate_history(meta, swaps, blocks, return_report=True)
        # validate_history checks all Swap/Mint/Burn transitions in global order.
        manifest = dict(
            provider="Pinax",
            quote_compatibility=fidelity,
            liquidity_events=len(liquidity),
            liquidity_model=MODEL if liquidity else None,
            organic_counterfactual="Fixed observed input, requoted output; router intent is not inferred",
            config=cfg,
            config_sha256=sha256(config),
            files=inventory,
            anchor_block=anchor[0][0],
            anchor_log_index=anchor[0][1],
            swaps=len(swaps),
            blocks=len(blocks),
            completeness="Provider export attestation; hashes do not prove omitted-log absence",
            finality_witness=headers[ordered[-1]],
        )
        encoded = json.dumps(manifest, indent=2) + "\n"
        budget.reserve(
            int(
                swaps.memory_usage(deep=True).sum() * 3
                + blocks.memory_usage(deep=True).sum() * 3
                + len(encoded)
                + 65536
            )
        )
        save_history(output, meta, swaps, blocks)
        (output / "import_manifest.json").write_text(encoded)
        budget.check(disk=True)
    return manifest


def main():
    """Parse arguments and import one pool history."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = import_history(args.config, args.output)
    print(json.dumps({k: result[k] for k in ("provider", "swaps", "blocks")}))


if __name__ == "__main__":
    main()
