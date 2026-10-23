"""Bounded, resumable HTTP-range extraction of the fixed Pinax study snapshot.

Downloads only column chunks that overlap the block window, verifies ETags and
checksums, and writes "acquisition.json" after all outputs pass checks.
"""

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import yaml

from .. import ROOT, pinax_date
from ..protocol import sha256
from ..resources import Budget, BudgetExceeded
from ..study import END, END_TS, START, START_TS

FIRST = 126728031
LAST_HEADER = END + 10000
PINAX = "https://parquet.riv-dev1.pinax.io"
BINANCE = "https://data.binance.vision"
COLUMNS = {
    "blocks": ["block_num", "block_id", "parent_id", "timestamp", "lib_num"],
    "logs": [
        "block_num",
        "block_id",
        "timestamp",
        "address",
        "tx_hash",
        "tx_index",
        "block_index",
        "topic0",
        "data",
    ],
    "transactions": ["block_num", "block_id", "timestamp", "hash", "index", "from"],
}


def write_json(path, value):
    """Atomically write "value" as pretty JSON to "path".

    Args:
        path: Destination file path.
        value: JSON-serializable object.
    """
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


class Fetcher:
    """Persist traffic accounting and verified range caches across retries.

    Tracks cumulative network bytes and request count in "usage.json" so a
    resumed download keeps honoring the global traffic and request budgets.
    """

    def __init__(self, root, budget, network_bytes, client=None):
        """Initialize accounting state, work directory, and HTTP client.

        Args:
            root: Work directory for "usage.json" and range caches.
            budget: Admission budget used to check disk/memory ceilings.
            network_bytes: Cumulative network byte limit for this session.
            client: Optional prebuilt :class:`httpx.Client`.
        """
        self.root, self.budget, self.limit = Path(root), budget, network_bytes
        self.root.mkdir(parents=True, exist_ok=True)
        self.usage_path = self.root / "usage.json"
        self.usage = (
            json.loads(self.usage_path.read_text())
            if self.usage_path.exists()
            else dict(network_bytes=0, requests=0)
        )
        self.client = client or httpx.Client(
            timeout=60, follow_redirects=False, headers={"Accept-Encoding": "identity"}
        )

    def request(self, url, maximum, *, headers=None, status=200):
        """Stream a GET request, enforce budgets, and retry transient failures.

        Args:
            url: Absolute HTTPS URL on an allow-listed public archive host.
            maximum: Maximum number of response bytes permitted.
            headers: Optional request headers.
            status: Expected HTTP status code.

        Returns:
            Tuple of the response body bytes and the response headers.

        Raises:
            ValueError: For disallowed hosts or unexpected status codes.
            BudgetExceeded: If a traffic or request budget is exhausted.
            RuntimeError: If all retry attempts fail.
        """
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc not in {
            urlparse(PINAX).netloc,
            urlparse(BINANCE).netloc,
        }:
            raise ValueError("Only the declared public archive hosts are allowed")
        for attempt in range(3):
            self.budget.check(disk=True)
            if self.usage["network_bytes"] + maximum > self.limit:
                raise BudgetExceeded(
                    "Cumulative download traffic budget exceeded; resume with an explicit larger --network-gib"
                )
            if self.usage["requests"] >= 25000:
                raise BudgetExceeded("25,000-request budget exceeded")
            self.usage["requests"] += 1
            try:
                with self.client.stream("GET", url, headers=headers) as response:
                    if response.status_code in {429, 502, 503, 504} and attempt < 2:
                        time.sleep(2**attempt)
                        continue
                    if response.status_code != status:
                        raise ValueError(
                            f"HTTP {response.status_code}: expected {status} for {url}; no alternate snapshot or RPC fallback"
                        )
                    chunks, received = [], 0
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        received += len(chunk)
                        self.usage["network_bytes"] += len(chunk)
                        self.budget.check()
                        if (
                            received > maximum
                            or self.usage["network_bytes"] > self.limit
                        ):
                            raise BudgetExceeded(
                                "Response exceeded its declared byte budget"
                            )
                        chunks.append(chunk)
                    return b"".join(chunks), dict(response.headers)
            finally:
                write_json(self.usage_path, self.usage)
        raise RuntimeError("Request retries exhausted")

    def read_range(self, obj, start, length):
        """Return a verified byte range, reusing a strong-ETag cache.

        Cached ranges are re-verified by SHA-256 and ETag; fresh ranges are
        fetched with an "If-Match" guard and written only after checks pass.

        Args:
            obj: Object dict with "url", "bytes", "etag", and "id".
            start: Byte offset of the range.
            length: Number of bytes to fetch.

        Returns:
            The exact "length" bytes for the range.

        Raises:
            ValueError: For invalid/corrupt ranges, missing strong ETags, or a
                server that does not honor the requested byte range.
        """
        if length == 0:
            return b""
        if start < 0 or length < 0 or start + length > obj["bytes"]:
            raise ValueError("Invalid object byte range")
        folder = self.root / "cache" / obj["id"]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{start}-{length}.bin"
        metadata_path = path.with_suffix(".json")
        if path.exists() and metadata_path.exists():
            metadata = json.loads(metadata_path.read_text())
            data = path.read_bytes()
            if (
                len(data) != length
                or hashlib.sha256(data).hexdigest() != metadata["sha256"]
            ):
                raise ValueError(f"Corrupt range cache: {path}")
            if obj.get("etag") and obj["etag"] != metadata["etag"]:
                raise ValueError("Cached range belongs to a different object version")
            obj["etag"] = metadata["etag"]
            return data
        headers = {"Range": f"bytes={start}-{start + length - 1}"}
        if obj.get("etag"):
            headers["If-Match"] = obj["etag"]
        data, response = self.request(obj["url"], length, headers=headers, status=206)
        if (
            response.get("content-range")
            != f"bytes {start}-{start + length - 1}/{obj['bytes']}"
            or len(data) != length
        ):
            raise ValueError("Server did not honor the exact byte range/object size")
        etag = response.get("etag")
        if (
            not etag
            or etag.startswith("W/")
            or (obj.get("etag") and obj["etag"] != etag)
        ):
            raise ValueError("Missing/changed strong object ETag")
        obj["etag"] = etag
        self.budget.reserve(length + 1024)
        temporary = path.with_suffix(".part")
        temporary.write_bytes(data)
        temporary.replace(path)
        write_json(
            metadata_path, dict(etag=etag, sha256=hashlib.sha256(data).hexdigest())
        )
        return data

    def close(self):
        """Close the underlying HTTP client."""
        self.client.close()


class RangeFile(io.RawIOBase):
    """Lazily fetch Parquet byte ranges as a seekable file object."""

    def __init__(self, fetcher, obj):
        """Initialize the position at byte 0.

        Args:
            fetcher: :class:`Fetcher` used to satisfy range reads.
            obj: Object dict describing the remote Parquet object.
        """
        self.fetcher, self.obj, self.position = fetcher, obj, 0

    def readable(self):
        """Return "True" (always readable)."""
        return True

    def seekable(self):
        """Return "True" (supports seeking)."""
        return True

    def tell(self):
        """Return the current byte offset."""
        return self.position

    def seek(self, offset, whence=os.SEEK_SET):
        """Move the byte offset relative to "whence".

        Args:
            offset: Byte offset relative to "whence".
            whence: One of :data:`os.SEEK_SET`, :data:`os.SEEK_CUR`,
                :data:`os.SEEK_END`.

        Returns:
            The new byte offset.

        Raises:
            ValueError: If the resulting position is negative.
        """
        position = offset + (
            0
            if whence == os.SEEK_SET
            else self.position
            if whence == os.SEEK_CUR
            else self.obj["bytes"]
        )
        if position < 0:
            raise ValueError("Negative seek")
        self.position = position
        return position

    def read(self, size=-1):
        """Read up to "size" bytes starting at the current offset.

        Args:
            size: Maximum number of bytes; "-1" reads to the object end.

        Returns:
            Bytes fetched from the remote range cache.

        Raises:
            BudgetExceeded: If more than 64 MiB is requested in one call.
        """
        size = (
            self.obj["bytes"] - self.position
            if size < 0
            else min(size, self.obj["bytes"] - self.position)
        )
        if size > 64 * 1024**2:
            raise BudgetExceeded("Parquet requested more than 64 MiB at once")
        data = self.fetcher.read_range(self.obj, self.position, max(0, size))
        self.position += len(data)
        return data


def metadata(fetcher, obj):
    """Fetch and cache the Parquet footer, verifying it is a Parquet object.

    Args:
        fetcher: :class:`Fetcher` for range reads and budget accounting.
        obj: Object dict (mutated with "metadata_sha256").

    Returns:
        A :class:`pyarrow.parquet.FileMetaData`.

    Raises:
        ValueError: If the object is not Parquet or its footer is oversized.
    """
    path = fetcher.root / "metadata" / (obj["id"] + ".parquet")
    if path.exists() and obj.get("metadata_sha256"):
        if sha256(path) != obj["metadata_sha256"]:
            raise ValueError("Parquet metadata cache changed")
        return pq.read_metadata(path)
    tail = fetcher.read_range(obj, obj["bytes"] - 8, 8)
    if tail[-4:] != b"PAR1":
        raise ValueError("Not an unencrypted Parquet object")
    length = int.from_bytes(tail[:4], "little") + 8
    if not 8 < length <= min(4 * 1024**2, obj["bytes"] - 4):
        raise ValueError("Parquet footer exceeds 4 MiB metadata budget")
    footer = fetcher.read_range(obj, obj["bytes"] - length, length)
    path.parent.mkdir(parents=True, exist_ok=True)
    fetcher.budget.reserve(length + 4)
    path.write_bytes(b"PAR1" + footer)
    obj["metadata_sha256"] = sha256(path)
    return pq.read_metadata(path)


def relevant_group(group, table):
    """Return whether a Parquet row group may overlap the block window.

    Args:
        group: A :class:`pyarrow.parquet.RowGroupMetaData`.
        table: One of "blocks", "logs", "transactions".

    Returns:
        "True" if the group's "block_num" range can intersect the target.

    Raises:
        ValueError: If the group lacks a "block_num" column.
    """
    column = next(
        (
            group.column(i)
            for i in range(group.num_columns)
            if group.column(i).path_in_schema == "block_num"
        ),
        None,
    )
    if column is None:
        raise ValueError("Missing block_num")
    stats = column.statistics
    upper = LAST_HEADER if table == "blocks" else END
    return (
        stats is None
        or not stats.has_min_max
        or (stats.max >= FIRST and stats.min <= upper)
    )


def make_plan(snapshot, fetcher, path):
    """Build or reload the cached download plan for the snapshot.

    Enumerates active files that overlap the block window, filters them to the
    native columns, and estimates projected column-chunk traffic.

    Args:
        snapshot: The frozen "pinax_snapshot.json" contents.
        fetcher: :class:`Fetcher` for metadata range reads.
        path: Destination path for "download_plan.json".

    Returns:
        The plan dict with "objects" and traffic estimates.
    """
    guard = fetcher.root / "plan.sha256"
    if path.exists():
        if not guard.exists() or sha256(path) != guard.read_text().strip():
            raise ValueError("Download plan changed or is incomplete")
        return json.loads(path.read_text())
    objects, projected = [], 0
    for table in COLUMNS:
        upper = LAST_HEADER if table == "blocks" else END
        for item in sorted(
            snapshot["tables"][table]["active_files"], key=lambda x: x["path"]
        ):
            stats = item["stats"]
            if (
                stats["maxValues"]["block_num"] < FIRST
                or stats["minValues"]["block_num"] > upper
            ):
                continue
            url = f"{snapshot['endpoint']}/{snapshot['bucket']}/{table}/{quote(item['path'], safe='/=')}"
            obj = dict(
                table=table,
                path=item["path"],
                url=url,
                bytes=item["bytes"],
                id=hashlib.sha256(url.encode()).hexdigest(),
            )
            meta = metadata(fetcher, obj)
            if "fork_step" in meta.schema.names or not set(COLUMNS[table]).issubset(
                meta.schema.names
            ):
                raise ValueError(
                    "Native schema drift or noncanonical fork-stream input"
                )
            obj["groups"] = [
                i
                for i in range(meta.num_row_groups)
                if relevant_group(meta.row_group(i), table)
            ]
            obj["projected_bytes"] = sum(
                meta.row_group(i).column(k).total_compressed_size
                for i in obj["groups"]
                for k in range(meta.row_group(i).num_columns)
                if meta.row_group(i).column(k).path_in_schema in COLUMNS[table]
            )
            projected += obj["projected_bytes"]
            objects.append(obj)
            print(f"Plan: {table} object {len(objects)}", flush=True)
    result = dict(
        objects=objects,
        full_object_bytes=sum(x["bytes"] for x in objects),
        projected_column_bytes=projected,
        traffic_upper_estimate_bytes=int(projected * 1.1) + 16 * 1024**2,
        note="Upper estimate reads every relevant projected column chunk. Address/hash filters can reduce it; retries add traffic. No whole-file download.",
    )
    write_json(path, result)
    guard.write_text(sha256(path) + "\n")
    return result


def normalize_hex(values):
    """Normalize binary/string hex fields to lowercase "0x"-prefixed strings.

    Args:
        values: A :class:`pyarrow.ChunkedArray` of bytes or strings.

    Returns:
        A string array with every value lowercase and "0x"-prefixed.

    Raises:
        ValueError: If the array is neither binary nor string typed.
    """
    if pa.types.is_binary(values.type) or pa.types.is_large_binary(values.type):
        return pa.array(
            ["0x" + v.hex() if v is not None else None for v in values.to_pylist()]
        )
    if not (pa.types.is_string(values.type) or pa.types.is_large_string(values.type)):
        raise ValueError("Expected hex string or binary native field")
    lowered = pc.utf8_lower(values)
    return pc.if_else(
        pc.starts_with(lowered, "0x"),
        lowered,
        pc.binary_join_element_wise("0x", lowered, ""),
    )


def extract_object(fetcher, obj, pairs, transactions, max_rows):
    """Extract and filter one Parquet object into a local shard.

    Reads only the row groups that overlap the block window, applies the
    address/hash filter in Arrow, and writes the selected rows to disk.

    Args:
        fetcher: :class:`Fetcher` for range reads and budgets.
        obj: Object dict describing the remote Parquet file.
        pairs: String array of pool pair addresses (for logs).
        transactions: String array of needed transaction hashes.
        max_rows: Selected-row budget across the object.

    Returns:
        Tuple of the shard path and its selected row count.
    """
    folder = fetcher.root / "shards"
    folder.mkdir(exist_ok=True)
    path = folder / (obj["id"] + ".parquet")
    checkpoint = path.with_suffix(".json")
    if checkpoint.exists() and path.exists():
        done = json.loads(checkpoint.read_text())
        if done["sha256"] != sha256(path) or done["etag"] != obj["etag"]:
            raise ValueError("Completed extraction shard changed")
        return path, done["rows"]
    meta = metadata(fetcher, obj)
    source = pq.ParquetFile(RangeFile(fetcher, obj), metadata=meta, pre_buffer=False)
    table = obj["table"]
    schema = pa.schema([meta.schema.to_arrow_schema().field(c) for c in COLUMNS[table]])
    for field in ("address", "tx_hash", "hash"):
        if field in schema.names:
            schema = schema.set(
                schema.get_field_index(field),
                pa.field(field, pa.string(), nullable=True),
            )
    temporary = path.with_suffix(".part")
    count = 0
    with pq.ParquetWriter(temporary, schema, compression="zstd") as writer:
        for i in obj["groups"]:
            fetcher.budget.check(disk=True)
            probe_columns = ["block_num"] + (
                ["address"]
                if table == "logs"
                else ["hash"]
                if table == "transactions"
                else []
            )
            probe = source.read_row_group(i, columns=probe_columns, use_threads=False)
            upper = LAST_HEADER if table == "blocks" else END
            mask = pc.and_(
                pc.greater_equal(probe["block_num"], FIRST),
                pc.less_equal(probe["block_num"], upper),
            )
            if table == "logs":
                mask = pc.and_(
                    mask, pc.is_in(normalize_hex(probe["address"]), value_set=pairs)
                )
            elif table == "transactions":
                mask = pc.and_(
                    mask, pc.is_in(normalize_hex(probe["hash"]), value_set=transactions)
                )
            if not pc.any(mask).as_py():
                continue
            selected = source.read_row_group(
                i, columns=COLUMNS[table], use_threads=False
            ).filter(mask)
            for field in ("address", "tx_hash", "hash"):
                if field in selected.column_names:
                    selected = selected.set_column(
                        selected.schema.get_field_index(field),
                        field,
                        normalize_hex(selected[field]),
                    )
            count += len(selected)
            if count > max_rows:
                raise BudgetExceeded("Selected-row budget exceeded")
            fetcher.budget.reserve(selected.nbytes + 65536)
            writer.write_table(selected.cast(schema))
    source.close()
    temporary.replace(path)
    write_json(checkpoint, dict(rows=count, sha256=sha256(path), etag=obj["etag"]))
    shutil.rmtree(fetcher.root / "cache" / obj["id"], ignore_errors=True)
    return path, count


def merge_shards(paths, output, budget, *, column=None, accepted=None):
    """Merge shards into one Parquet file, optionally filtering by "column".

    Args:
        paths: Source shard paths.
        output: Destination Parquet path.
        budget: Admission budget for disk accounting.
        column: Optional column name to filter on.
        accepted: Optional value set that "column" must belong to.
    """
    temporary = output.with_suffix(".part")
    output.parent.mkdir(parents=True, exist_ok=True)
    with pq.ParquetWriter(
        temporary, pq.read_schema(paths[0]), compression="zstd"
    ) as writer:
        for path in paths:
            for batch in pq.ParquetFile(path).iter_batches(batch_size=8192):
                budget.check(disk=True)
                if column:
                    batch = batch.filter(
                        pc.is_in(batch.column(column), value_set=accepted)
                    )
                if batch.num_rows:
                    budget.reserve(batch.nbytes + 65536)
                    writer.write_batch(batch)
    temporary.replace(output)


def prices(fetcher, output, supplied=None):
    """Build the causal "ts,quote_usd" price CSV for the interval.

    Downloads checksum-verified BNBUSDT minute archives unless a local CSV is
    supplied, then verifies second-resolution backward coverage.

    Args:
        fetcher: :class:`Fetcher` for archive requests.
        output: Destination "bnb_usd.csv" path.
        supplied: Optional path to an audited local price CSV.

    Returns:
        List of price source evidence dicts.
    """
    records, evidence = [], []
    if supplied:
        if Path(supplied).stat().st_size > 32 * 1024**2:
            raise ValueError("Supplied price CSV exceeds 32 MiB")
        frame = pd.read_csv(supplied)
        evidence.append(dict(local_sha256=sha256(supplied)))
    else:
        for day in pd.date_range(
            pd.to_datetime(START_TS, unit="s", utc=True),
            pd.to_datetime(END_TS, unit="s", utc=True),
            freq="D",
        ):
            name = f"BNBUSDT-1m-{day:%Y-%m-%d}.zip"
            url = f"{BINANCE}/data/spot/daily/klines/BNBUSDT/1m/{name}"
            cache = fetcher.root / name
            checksum_path = cache.with_suffix(".checksum")
            if not checksum_path.exists():
                checksum, _ = fetcher.request(url + ".CHECKSUM", 4096)
                checksum_path.write_bytes(checksum)
            expected = checksum_path.read_text().split()[0]
            if not cache.exists():
                data, _ = fetcher.request(url, 4 * 1024**2)
                if hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError("Binance archive checksum mismatch")
                fetcher.budget.reserve(len(data))
                cache.write_bytes(data)
            if sha256(cache) != expected:
                raise ValueError("Cached price archive checksum mismatch")
            with zipfile.ZipFile(cache) as archive:
                info = archive.getinfo(name.replace(".zip", ".csv"))
                if info.file_size > 16 * 1024**2:
                    raise ValueError("Price CSV exceeds uncompressed budget")
                data = pd.read_csv(
                    io.BytesIO(archive.read(info)), header=None, dtype=str
                )
            if not str(data.iloc[0, 0]).isdigit():
                data = data.iloc[1:]
            for stamp, opening in zip(data.iloc[:, 0], data.iloc[:, 1], strict=True):
                stamp = int(stamp)
                divisor = (
                    1_000_000 if stamp >= 10**15 else 1000 if stamp >= 10**12 else 1
                )
                if stamp % divisor:
                    raise ValueError("Fractional-second archive candle timestamp")
                records.append(dict(ts=stamp // divisor, quote_usd=float(opening)))
            evidence.append(
                dict(
                    url=url,
                    sha256=expected,
                    conversion="BNBUSDT minute open; USDT=USD assumption",
                )
            )
        frame = pd.DataFrame(records)
    frame = frame[["ts", "quote_usd"]].sort_values("ts")
    if (
        frame.ts.duplicated().any()
        or not (frame.ts % 1 == 0).all()
        or not np.isfinite(frame.quote_usd).all()
        or (frame.quote_usd <= 0).any()
    ):
        raise ValueError("Invalid price timestamps or values")
    frame["ts"] = frame.ts.astype("int64")
    required = pd.DataFrame({"ts": np.arange(START_TS, END_TS + 1)})
    if (
        pd.merge_asof(required, frame, on="ts", direction="backward", tolerance=60)
        .quote_usd.isna()
        .any()
    ):
        raise ValueError("Incomplete causal price coverage")
    frame = frame[(frame.ts >= START_TS - 60) & (frame.ts <= END_TS)]
    temporary = output.with_suffix(".part")
    frame.to_csv(temporary, index=False)
    temporary.replace(output)
    return evidence


def download(
    config,
    output,
    *,
    plan_only=False,
    network_gib=32,
    disk_gib=16,
    memory_mb=8192,
    wall_seconds=14400,
    max_selected_rows=2_000_000,
    prices_file=None,
):
    """Run the bounded, resumable acquisition of the pinned Pinax snapshot.

    Validates the frozen snapshot/cohort identities, builds or reloads the
    download plan, extracts per-pool logs and referenced transactions, and
    writes "acquisition.json" only after all outputs pass checks.

    Args:
        config: Study config directory containing "pools.yaml" and the frozen
            "study_evidence/pinax_snapshot.json".
        output: Raw output directory.
        plan_only: If "True", stop after building the traffic plan.
        network_gib: Cumulative network byte budget in GiB.
        disk_gib: Output/cache disk budget in GiB.
        memory_mb: Process memory budget in MiB.
        wall_seconds: Wall-clock budget per invocation.
        max_selected_rows: Per-table selected-row budget.
        prices_file: Optional audited local price CSV path.

    Returns:
        The acquisition result dict (or the plan when "plan_only").
    """
    config, output = Path(config).resolve(), Path(output).resolve()
    snapshot_path = config / "study_evidence/pinax_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text())
    cohort = yaml.safe_load((config / "pools.yaml").read_text())
    if snapshot["endpoint"] != PINAX or snapshot["bucket"] != "bsc":
        raise ValueError("Expected the pinned public BSC snapshot")
    size = len(cohort["pools"])
    screening = cohort.get("selection", {}).get("purpose") == "reserve_screening"
    if (not 1 <= size <= 36 or (size != 36 and not screening)) or any(
        p["start_block"] != START or p["end_block"] != END for p in cohort["pools"]
    ):
        raise ValueError(
            "Expected the declared study or a bounded reserve-screening cohort"
        )
    if (
        len({p["id"] for p in cohort["pools"]}) != size
        or len({p["pair"] for p in cohort["pools"]}) != size
        or any(
            not re.fullmatch(r"pool_[0-9]{2}", p["id"])
            or not re.fullmatch(r"0x[0-9a-f]{40}", p["pair"])
            for p in cohort["pools"]
        )
    ):
        raise ValueError("Invalid or duplicate pool identities")
    if min(network_gib, disk_gib, memory_mb, wall_seconds, max_selected_rows) <= 0:
        raise ValueError("Resource limits must be positive")
    identity = dict(
        snapshot_sha256=sha256(snapshot_path),
        cohort_sha256=sha256(config / "pools.yaml"),
        prices_sha256=sha256(prices_file) if prices_file else None,
    )
    work = output / ".download"
    identity_path = work / "identity.json"
    if identity_path.exists():
        if json.loads(identity_path.read_text()) != identity:
            raise ValueError("Resume inputs changed; use a new output directory")
    else:
        if output.exists() and any(output.iterdir()):
            raise ValueError(
                "Output must be empty or belong to a matching resumable download"
            )
        work.mkdir(parents=True)
        write_json(identity_path, identity)
    complete = output / "acquisition.json"
    if complete.exists():
        result = json.loads(complete.read_text())
        if (
            result.get("complete") is not True
            or result.get("identity") != identity
            or len(result.get("outputs", {})) != 2 + 2 * size
            or any(sha256(output / p) != h for p, h in result["outputs"].items())
        ):
            raise ValueError("Completed acquisition was modified")
        return result
    limits = dict(
        memory_mb=memory_mb, disk_mb=disk_gib * 1024, wall_seconds=wall_seconds
    )
    with Budget(limits, output) as budget:
        fetcher = Fetcher(work, budget, int(network_gib * 1024**3))
        try:
            plan = make_plan(snapshot, fetcher, output / "download_plan.json")
            print(
                json.dumps({k: v for k, v in plan.items() if k != "objects"}, indent=2),
                flush=True,
            )
            if plan_only:
                return plan
            if plan["traffic_upper_estimate_bytes"] > network_gib * 1024**3:
                raise BudgetExceeded(
                    "Projected traffic exceeds --network-gib; inspect download_plan.json before increasing the limit"
                )
            # Prices fail early if the public daily archive is unavailable.
            price_sources = prices(fetcher, output / "bnb_usd.csv", prices_file)
            pairs = pa.array([p["pair"] for p in cohort["pools"]])
            shards, tx_hashes, counts = {}, pa.array([], type=pa.string()), {}
            for table in COLUMNS:
                shards[table], counts[table] = [], 0
                objects = [o for o in plan["objects"] if o["table"] == table]
                if not objects:
                    raise ValueError(f"No pinned objects for {table}")
                for index, obj in enumerate(objects, 1):
                    print(
                        f"Extracting {table} {index}/{len(objects)}; downloaded {fetcher.usage['network_bytes'] / 1024**3:.3f} GiB",
                        flush=True,
                    )
                    path, count = extract_object(
                        fetcher, obj, pairs, tx_hashes, max_selected_rows
                    )
                    counts[table] += count
                    if counts[table] > max_selected_rows:
                        raise BudgetExceeded(
                            f"Selected {table} rows exceed --max-selected-rows"
                        )
                    shards[table].append(path)
                if table == "logs":
                    needed = set()
                    for path in shards[table]:
                        for batch in pq.ParquetFile(path).iter_batches(
                            columns=["block_num", "tx_hash"], batch_size=8192
                        ):
                            batch = batch.filter(
                                pc.greater_equal(batch.column("block_num"), START)
                            )
                            needed.update(batch.column("tx_hash").to_pylist())
                    tx_hashes = pa.array(sorted(needed), type=pa.string())
            merge_shards(shards["blocks"], output / "blocks.parquet", budget)
            headers = (
                pq.read_table(output / "blocks.parquet")
                .to_pandas()
                .sort_values("block_num")
            )
            if (
                headers.block_num.duplicated().any()
                or headers.block_num.iloc[0] != FIRST
                or headers.block_num.iloc[-1] < END
                or (headers.block_num.diff().dropna() != 1).any()
            ):
                raise ValueError("Pinned headers are duplicated or incomplete")
            if int(headers.lib_num.iloc[-1]) < END:
                raise ValueError(
                    "Pinned header suffix does not provide the required LIB witness"
                )
            headers.to_parquet(
                output / "blocks.parquet", index=False, compression="zstd"
            )
            for pool in cohort["pools"]:
                folder = output / pool["id"]
                merge_shards(
                    shards["logs"],
                    folder / "logs.parquet",
                    budget,
                    column="address",
                    accepted=pa.array([pool["pair"]]),
                )
                logs = pq.read_table(
                    folder / "logs.parquet", columns=["block_num", "tx_hash"]
                )
                logs = logs.filter(pc.greater_equal(logs["block_num"], START))
                hashes = pc.unique(logs["tx_hash"])
                merge_shards(
                    shards["transactions"],
                    folder / "transactions.parquet",
                    budget,
                    column="hash",
                    accepted=hashes,
                )
            outputs = [output / "blocks.parquet", output / "bnb_usd.csv"] + [
                output / p["id"] / name
                for p in cohort["pools"]
                for name in ("logs.parquet", "transactions.parquet")
            ]
            budget.check(disk=True)
            result = dict(
                complete=True,
                identity=identity,
                source="Pinax column-range extraction",
                source_objects=plan["objects"],
                selected_rows=counts,
                price_sources=price_sources,
                network_bytes=fetcher.usage["network_bytes"],
                completed_utc=datetime.datetime.now(datetime.UTC).isoformat(),
                outputs={str(p.relative_to(output)): sha256(p) for p in outputs},
            )
            write_json(complete, result)
            (output / "INCOMPLETE.json").unlink(missing_ok=True)
            shutil.rmtree(work / "shards", ignore_errors=True)
            shutil.rmtree(work / "cache", ignore_errors=True)
            return result
        finally:
            fetcher.close()


def main():
    """Parse arguments and run the plan or full download."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ROOT / "config"))
    parser.add_argument("--output", default=f"data/pinax_{pinax_date()}/raw")
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Fetch only metadata and estimate projected traffic",
    )
    parser.add_argument("--network-gib", type=float, default=32)
    parser.add_argument("--disk-gib", type=float, default=16)
    parser.add_argument("--memory-mb", type=int, default=8192)
    parser.add_argument("--wall-seconds", type=int, default=14400)
    parser.add_argument("--max-selected-rows", type=int, default=2_000_000)
    parser.add_argument(
        "--prices-file",
        help="Use a supplied causal ts,quote_usd CSV instead of downloading price archives",
    )
    args = parser.parse_args()
    result = download(
        args.config,
        args.output,
        plan_only=args.plan,
        network_gib=args.network_gib,
        disk_gib=args.disk_gib,
        memory_mb=args.memory_mb,
        wall_seconds=args.wall_seconds,
        max_selected_rows=args.max_selected_rows,
        prices_file=args.prices_file,
    )
    print(
        "Download plan ready."
        if args.plan
        else f"Acquisition complete: {len(result['outputs'])} files."
    )


if __name__ == "__main__":
    main()
