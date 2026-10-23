"""Exercise archive acquisition without network access or historical claims."""

import contextlib
import hashlib
import io
import json
import zipfile

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from experiments.data import download_pinax as d


class Budget:
    def check(self, **kwargs):
        pass

    def reserve(self, size):
        pass


class Client:
    def __init__(self, objects):
        self.objects, self.calls = objects, 0

    @contextlib.contextmanager
    def stream(self, method, url, headers=None):
        self.calls += 1
        data = self.objects[url]
        response_headers = {"etag": '"version1"'}
        status = 200
        if headers and "Range" in headers:
            start, end = map(int, headers["Range"][6:].split("-"))
            response_headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
            data = data[start : end + 1]
            status = 206
        yield httpx.Response(status, content=data, headers=response_headers)

    def close(self):
        pass


def test_range_cache_integrity_and_budget(tmp_path):
    url = d.PINAX + "/example"
    client = Client({url: b"abcdefgh"})
    fetcher = d.Fetcher(tmp_path, Budget(), 8, client)
    obj = dict(id="test", bytes=8, url=url)
    assert fetcher.read_range(obj, 0, 4) == b"abcd"
    assert fetcher.read_range(obj, 0, 4) == b"abcd"
    assert client.calls == 1
    resumed = d.Fetcher(tmp_path, Budget(), 6, client)
    with pytest.raises(d.BudgetExceeded):
        resumed.read_range(obj, 4, 4)
    (tmp_path / "cache/test/0-4.bin").write_bytes(b"xxxx")
    with pytest.raises(ValueError, match="Corrupt"):
        fetcher.read_range(obj, 0, 4)


@pytest.mark.parametrize("fault", ["status", "etag", "range"])
def test_rejects_invalid_range_responses(tmp_path, fault):
    class BadClient:
        @contextlib.contextmanager
        def stream(self, *args, **kwargs):
            yield httpx.Response(
                200 if fault == "status" else 206,
                content=b"abcd",
                headers={
                    "etag": '"changed"' if fault == "etag" else '"version1"',
                    "content-range": "bytes 0-3/9"
                    if fault == "range"
                    else "bytes 0-3/8",
                },
            )

    fetcher = d.Fetcher(tmp_path, Budget(), 100, BadClient())
    with pytest.raises(ValueError):
        fetcher.read_range(
            dict(id="test", bytes=8, url=d.PINAX + "/test", etag='"version1"'), 0, 4
        )


def test_price_archive_open_microseconds_and_gap(tmp_path, monkeypatch):
    start = 1791676800
    monkeypatch.setattr(d, "START_TS", start)
    monkeypatch.setattr(d, "END_TS", start + 119)
    name = "BNBUSDT-1m-2026-10-11.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(
            name.replace(".zip", ".csv"),
            f"{start * 1000000},600,900,500,800\n{(start + 60) * 1000000},601,900,500,800\n",
        )
    blob = buffer.getvalue()
    url = f"{d.BINANCE}/data/spot/daily/klines/BNBUSDT/1m/{name}"
    client = Client(
        {url: blob, url + ".CHECKSUM": hashlib.sha256(blob).hexdigest().encode()}
    )
    fetcher = d.Fetcher(tmp_path / "cache", Budget(), 10**7, client)
    output = tmp_path / "prices.csv"
    d.prices(fetcher, output)
    assert pd.read_csv(output).quote_usd.tolist() == [600, 601]
    pd.DataFrame({"ts": [start], "quote_usd": [600]}).to_csv(output, index=False)
    with pytest.raises(ValueError, match="coverage"):
        d.prices(fetcher, tmp_path / "bad.csv", output)


@pytest.mark.parametrize("pool_count", [36, 21])
def test_full_acquisition_resume_and_output_hashes(tmp_path, monkeypatch, pool_count):
    for key, value in dict(
        FIRST=100, START=101, END=102, LAST_HEADER=103, START_TS=1000, END_TS=1060
    ).items():
        monkeypatch.setattr(d, key, value)
    config = tmp_path / "config"
    (config / "study_evidence").mkdir(parents=True)
    pools = [
        dict(id=f"pool_{n:02}", pair=f"0x{n:040x}", start_block=101, end_block=102)
        for n in range(1, pool_count + 1)
    ]
    (config / "pools.yaml").write_text(
        yaml.safe_dump(
            dict(
                pools=pools,
                selection=dict(purpose="reserve_screening") if pool_count != 36 else {},
            )
        )
    )
    blocks = [
        dict(
            block_num=n,
            block_id=f"0x{n:064x}",
            parent_id=f"0x{n - 1:064x}",
            timestamp=n,
            lib_num=102,
        )
        for n in range(100, 104)
    ]
    logs, transactions = [], []
    for n, pool in enumerate(pools, 1):
        common = dict(block_num=101, block_id=f"0x{101:064x}", timestamp=101)
        tx = f"0x{n:064x}"
        logs.append(
            dict(
                common,
                address=pool["pair"].upper(),
                tx_hash=tx,
                tx_index=n,
                block_index=n + 50,
                topic0="0x00",
                data="0x00",
            )
        )
        transactions.append(dict(common, hash=tx, index=n, **{"from": pool["pair"]}))
    snapshot = dict(endpoint=d.PINAX, bucket="bsc", tables={})
    objects = {}
    for table, rows in dict(
        blocks=blocks, logs=logs, transactions=transactions
    ).items():
        buffer = io.BytesIO()
        pq.write_table(pa.Table.from_pylist(rows), buffer, row_group_size=16)
        blob = buffer.getvalue()
        objects[f"{d.PINAX}/bsc/{table}/part.parquet"] = blob
        snapshot["tables"][table] = dict(
            active_files=[
                dict(
                    path="part.parquet",
                    bytes=len(blob),
                    stats=dict(
                        minValues=dict(block_num=100), maxValues=dict(block_num=103)
                    ),
                )
            ]
        )
    (config / "study_evidence/pinax_snapshot.json").write_text(json.dumps(snapshot))
    prices = tmp_path / "prices.csv"
    pd.DataFrame(dict(ts=[1000, 1060], quote_usd=[600, 601])).to_csv(
        prices, index=False
    )
    client = Client(objects)
    monkeypatch.setattr(d.httpx, "Client", lambda **kwargs: client)
    extract = d.extract_object

    def interrupted(fetcher, obj, *args):
        if obj["table"] == "transactions":
            raise RuntimeError("simulated interruption")
        return extract(fetcher, obj, *args)

    monkeypatch.setattr(d, "extract_object", interrupted)
    output = tmp_path / "raw"
    with pytest.raises(RuntimeError, match="interruption"):
        d.download(config, output, prices_file=prices)
    monkeypatch.setattr(d, "extract_object", extract)
    result = d.download(config, output, prices_file=prices)
    assert result["complete"] and len(result["outputs"]) == 2 + 2 * pool_count
    assert pq.read_table(output / "pool_01/logs.parquet")[
        "block_index"
    ].to_pylist() == [51]
    assert (
        pq.read_table(output / f"pool_{pool_count:02}/transactions.parquet").num_rows
        == 1
    )
    calls = client.calls
    assert d.download(config, output, prices_file=prices) == result
    assert client.calls == calls
    (output / "bnb_usd.csv").write_text("changed")
    with pytest.raises(ValueError, match="modified"):
        d.download(config, output, prices_file=prices)
