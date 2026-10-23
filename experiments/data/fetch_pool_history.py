"""Legacy opt-in RPC downloader; official Path B uses data.import_pinax.

Retained for compatibility and shared Swap/Sync decoding. Never called by the
Pinax importer or experiment runner as a network fallback.
"""

import argparse
import json
import math
import os
import time
from pathlib import Path

import pandas as pd
from app.chain_monitoring.sources import BnbRpcSource
from eth_utils import keccak

from .history import save_history, validate_history

SWAP_TOPIC = (
    "0x" + keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex()
)
SYNC_TOPIC = "0x" + keccak(text="Sync(uint112,uint112)").hex()
READ_METHODS = {
    "eth_chainId",
    "eth_getBlockByNumber",
    "eth_getLogs",
    "eth_call",
    "eth_getTransactionByHash",
}


class CachedReadTransport:
    """Read-only JSON-RPC transport with a persistent on-disk response cache.

    Caches only deterministic ("immutable") responses and enforces a request
    budget and a per-second rate limit so the legacy downloader cannot issue
    unbounded or state-mutating calls.
    """

    def __init__(
        self, folder, *, max_requests=10000, requests_per_second=2, cache_mb=256
    ):
        """Initialize budgets, rate limiting, and the underlying HTTP transport.

        Args:
            folder: Directory for the persisted JSON response cache.
            max_requests: Hard cap on the total number of network requests.
            requests_per_second: Minimum interval between network requests.
            cache_mb: Maximum total on-disk cache size in MiB.

        Raises:
            ValueError: If any budget is not positive.
        """
        if min(max_requests, requests_per_second, cache_mb) <= 0:
            raise ValueError("Download budgets must be positive")
        self.max_requests = max_requests
        self.rate = requests_per_second
        self.cache_limit = int(cache_mb * 1024**2)
        self.requests = 0
        self.cache_hits = 0
        self.last_request = 0.0
        from app.chain_monitoring.sources import HttpxRpcTransport

        self.transport = HttpxRpcTransport(timeout=60)
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.cache_bytes = sum(p.stat().st_size for p in self.folder.glob("*.json"))

    def post(self, url, payload):
        """Return the JSON result for "payload", caching immutable responses.

        Args:
            url: RPC endpoint URL (deliberately excluded from persisted metadata).
            payload: JSON-RPC request whose "method" is read-only.

        Returns:
            The parsed JSON response body.

        Raises:
            ValueError: If the method is outside the read-only allowlist.
            RuntimeError: If the request or cache budget is exhausted.
        """
        import hashlib

        if payload["method"] not in READ_METHODS:
            raise ValueError("RPC method is outside the read-only allowlist")
        # Endpoint is deliberately excluded from persisted metadata.
        key = hashlib.sha256(
            json.dumps([payload["method"], payload["params"]], sort_keys=True).encode()
        ).hexdigest()
        path = self.folder / (key + ".json")
        immutable = payload["method"] != "eth_chainId" and not (
            payload["method"] == "eth_getBlockByNumber"
            and payload["params"][0] in ("latest", "finalized", "safe", "pending")
        )
        if immutable and path.exists():
            self.cache_hits += 1
            return json.loads(path.read_text())
        if self.requests >= self.max_requests:
            raise RuntimeError(
                "Read-only request budget reached; rerun with the same cache to resume"
            )
        delay = max(0, 1 / self.rate - (time.monotonic() - self.last_request))
        if delay:
            time.sleep(delay)
        self.last_request = time.monotonic()
        self.requests += 1
        result = self.transport.post(url, payload)
        if immutable and "error" not in result and result.get("result") is not None:
            encoded = json.dumps(result)
            if self.cache_bytes + len(encoded.encode()) > self.cache_limit:
                raise RuntimeError(
                    "RPC cache budget reached; increase the explicit cache budget or select a smaller preregistered range"
                )
            path.write_text(encoded)
            self.cache_bytes += len(encoded.encode())
        return result

    def close(self):
        """Close the underlying HTTP transport."""
        self.transport.close()


def words(data):
    """Decode ABI "data" into big-endian 256-bit words.

    Args:
        data: "0x"-prefixed hexadecimal ABI payload.

    Returns:
        List of decoded integer words.

    Raises:
        ValueError: If the payload length is not a multiple of 64 hex chars.
    """
    value = data.removeprefix("0x")
    if len(value) % 64:
        raise ValueError("Malformed ABI data")
    return [int(value[i : i + 64], 16) for i in range(0, len(value), 64)]


def decode_logs(logs, quote_index, block_times, traders):
    """Pair Sync/Swap logs into single-sided exact-input swap rows.

    A PancakeSwap V2 "Swap" is always preceded by a "Sync" in the same
    transaction, and that "Sync"'s reserves are the post-swap state.

    Args:
        logs: Raw pool logs for the interval.
        quote_index: Index (0 or 1) of the quote asset in the pair.
        block_times: Mapping from block number to Unix timestamp.
        traders: Mapping from transaction hash to initiating address.

    Returns:
        A :class:`pandas.DataFrame` with one row per qualified swap.

    Raises:
        ValueError: For reorged logs, unmatched Syncs, malformed payloads, or
            swaps outside the single-input exact-output model.
    """
    rows = []
    pending = {}
    for event in sorted(
        logs, key=lambda x: (int(x["blockNumber"], 16), int(x["logIndex"], 16))
    ):
        if event.get("removed"):
            raise ValueError("Removed/reorged log in history")
        topic = event["topics"][0].lower()
        tx = event["transactionHash"]
        if topic == SYNC_TOPIC:
            if tx in pending:
                raise ValueError(
                    "Unmatched Sync (liquidity change); select a swap-only interval"
                )
            pending[tx] = words(event["data"])
        elif topic == SWAP_TOPIC:
            if tx not in pending:
                raise ValueError("Swap is missing its preceding Sync")
            reserves = pending.pop(tx)
            a = words(event["data"])
            if len(a) != 4 or len(reserves) != 2:
                raise ValueError("Malformed Swap/Sync")
            qi, ti = a[quote_index], a[1 - quote_index]
            qo, to = a[2 + quote_index], a[3 - quote_index]
            if qi > 0 and to > 0 and ti == qo == 0:
                side, amount_in, amount_out = "BUY", qi, to
            elif ti > 0 and qo > 0 and qi == to == 0:
                side, amount_in, amount_out = "SELL", ti, qo
            else:
                raise ValueError(
                    "Flash/multi-input swap is outside the exact-input model"
                )
            block = int(event["blockNumber"], 16)
            rows.append(
                dict(
                    block=block,
                    ts=block_times[block],
                    tx_hash=tx,
                    log_index=int(event["logIndex"], 16),
                    trader=traders[tx].lower(),
                    side=side,
                    amount_in_raw=amount_in,
                    amount_out_raw=amount_out,
                    reserve_quote_after_raw=reserves[quote_index],
                    reserve_token_after_raw=reserves[1 - quote_index],
                )
            )
    if pending:
        raise ValueError(
            "Unmatched Sync: liquidity changes cannot be replayed as swaps"
        )
    return pd.DataFrame(rows)


def fetch(
    pair,
    start,
    end,
    quote,
    output,
    rpc_url,
    quote_prices=None,
    chunk_size=1000,
    *,
    max_requests=10000,
    requests_per_second=2,
    cache_mb=256,
):
    """Download and qualify a swap-only history over "[start, end]".

    Reads token metadata and initial reserves via "eth_call" at
    "start - 1", fetches pool logs and every intervening header, then
    validates and saves the history. Quote conversion supports USDT or WBNB.

    Args:
        pair: Address of the PancakeSwap V2 pair.
        start: First block (inclusive) of the replay interval.
        end: Last block (inclusive) of the replay interval.
        quote: Quote token address (USDT or WBNB).
        output: Output directory for the saved history.
        rpc_url: Archive-capable read-only RPC endpoint.
        quote_prices: Optional causal "ts,quote_usd" CSV for WBNB quotes.
        chunk_size: Block range per "eth_getLogs" call.
        max_requests: Request budget forwarded to the transport.
        requests_per_second: Rate limit forwarded to the transport.
        cache_mb: Cache budget forwarded to the transport.

    Raises:
        ValueError: For invalid ranges, non-BSC chains, non-finalized blocks,
            missing blocks/traders, or unsupported quote assets.
    """
    if start < 1 or end < start or chunk_size < 1:
        raise ValueError("Invalid block range")
    transport = CachedReadTransport(
        Path(output) / "rpc_cache",
        max_requests=max_requests,
        requests_per_second=requests_per_second,
        cache_mb=cache_mb,
    )
    source = BnbRpcSource(rpc_url, transport=transport)
    try:
        if int(source._call("eth_chainId", []), 16) != 56:
            raise ValueError("Expected BSC chain ID 56")
        finalized = source.get_block("finalized")
        if finalized is None or int(finalized["number"], 16) < end:
            raise ValueError("Requested range is not finalized")

        def call(address, data):
            return source.historical_call(to=address, data=data, block=start - 1)

        token0 = "0x" + call(pair, "0x0dfe1681")[-40:]
        token1 = "0x" + call(pair, "0xd21220a7")[-40:]
        tokens = [token0.lower(), token1.lower()]
        if quote.lower() not in tokens:
            raise ValueError("Quote token is not in the pair")
        qi = tokens.index(quote.lower())
        decimals = [words(call(t, "0x313ce567"))[0] for t in tokens]
        reserves = words(call(pair, "0x0902f1ac"))
        # End-of-(start-1) state avoids applying the first block twice.
        meta = dict(
            pair=pair.lower(),
            token0=tokens[0],
            token1=tokens[1],
            quote_index=qi,
            quote_decimals=decimals[qi],
            token_decimals=decimals[1 - qi],
            reserve_quote_raw=str(reserves[qi]),
            reserve_token_raw=str(reserves[1 - qi]),
            source="historical",
            start_block=start,
            end_block=end,
        )
        logs = []
        for left in range(start, end + 1, chunk_size):
            logs.extend(
                source.get_logs(
                    from_block=left,
                    to_block=min(end, left + chunk_size - 1),
                    addresses=[pair],
                )
            )
        block_rows = []
        for n in range(start, end + 1):
            b = source.get_block(n)
            if b is None:
                raise ValueError(f"Missing block {n}")
            block_rows.append(
                dict(
                    block=n,
                    ts=int(b["timestamp"], 16),
                    hash=b["hash"],
                    parent_hash=b["parentHash"],
                )
            )
        blocks = pd.DataFrame(block_rows)
        if any(
            blocks.parent_hash.iloc[i] != blocks.hash.iloc[i - 1]
            for i in range(1, len(blocks))
        ):
            raise ValueError("Inconsistent block hash chain")
        traders = {}
        for log in logs:
            if log["topics"][0].lower() == SWAP_TOPIC:
                tx = log["transactionHash"]
                if tx not in traders:
                    transaction = source.get_transaction_by_hash(tx)
                    if not transaction or not transaction.get("from"):
                        raise ValueError("Missing transaction sender")
                    traders[tx] = transaction["from"]
        swaps = decode_logs(
            logs, qi, dict(zip(blocks.block, blocks.ts, strict=False)), traders
        )
        if swaps.empty:
            raise ValueError("No swaps in selected interval")
        usdt = "0x55d398326f99059ff775485246999027b3197955"
        wbnb = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
        if quote.lower() == usdt:
            blocks["quote_usd"] = 1.0
            meta["usd_assumption"] = "USDT = 1 USD"
        elif quote.lower() == wbnb:
            if quote_prices:
                prices = pd.read_csv(quote_prices).sort_values("ts")
                tolerance = 60
                meta["usd_assumption"] = "Causal external price CSV, max age 60 seconds"
            else:
                from app.market_data.binance import BinanceBnbMarketProvider

                provider = BinanceBnbMarketProvider()
                try:
                    candles = provider.candles(
                        int(blocks.ts.iloc[0]) // 3600 * 3600,
                        int(blocks.ts.iloc[-1]) + 3600,
                    )
                finally:
                    provider.close()
                # The open is available at ts; using the hour's close at ts leaks future data.
                prices = pd.DataFrame(
                    [dict(ts=c.ts, quote_usd=float(c.open)) for c in candles]
                )
                if prices.empty:
                    raise ValueError("No historical BNB/USD candles returned")
                tolerance = 3600
                meta["usd_assumption"] = (
                    "Binance hourly BNBUSDT open, causal backward join"
                )
                prices.to_csv(Path(output) / "quote_prices.csv", index=False)
            # Every conversion uses information observable at the replay timestamp.
            blocks = pd.merge_asof(
                blocks,
                prices[["ts", "quote_usd"]],
                on="ts",
                direction="backward",
                tolerance=tolerance,
            )
            if blocks.quote_usd.isna().any():
                raise ValueError("Missing causal WBNB/USD candle prices")
        else:
            raise ValueError("Only USDT or WBNB is supported")
        swaps["quote_usd"] = swaps.block.map(
            dict(zip(blocks.block, blocks.quote_usd, strict=False))
        )
        validate_history(meta, swaps, blocks)
        save_history(output, meta, swaps, blocks)
    finally:
        source.close()
        (Path(output) / "access.json").write_text(
            json.dumps(
                dict(
                    network_requests=transport.requests,
                    cache_hits=transport.cache_hits,
                    cache_bytes=transport.cache_bytes,
                    rate_limit=transport.rate,
                ),
                indent=2,
            )
            + "\n"
        )


def main():
    """Parse arguments and run the legacy downloader or a no-request estimate."""
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("pair", "quote", "output"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--start", type=int, required=True)
    p.add_argument("--end", type=int, required=True)
    p.add_argument("--quote-prices")
    p.add_argument("--max-requests", type=int, default=10000)
    p.add_argument("--requests-per-second", type=float, default=2)
    p.add_argument("--cache-mb", type=float, default=256)
    p.add_argument(
        "--estimate-only",
        action="store_true",
        help="Estimate minimum RPC work without any network request",
    )
    args = p.parse_args()
    if args.estimate_only:
        blocks = args.end - args.start + 1
        if blocks <= 0 or args.requests_per_second <= 0:
            p.error("Invalid range or request rate")
        minimum = blocks + 8 + math.ceil(blocks / 1000)
        print(
            json.dumps(
                dict(
                    block_headers=blocks,
                    minimum_rpc_calls=minimum,
                    minimum_seconds=minimum / args.requests_per_second,
                    additional_transaction_sender_calls="one per distinct swap transaction",
                    fits_one_request_budget=minimum <= args.max_requests,
                    note="No request made; archive availability and provider quotas are not guaranteed.",
                ),
                indent=2,
            )
        )
        return
    endpoint = os.environ.get("BSC_READ_RPC_URL")
    if not endpoint:
        p.error("Set BSC_READ_RPC_URL to an archive-capable read-only endpoint")
    fetch(
        args.pair,
        args.start,
        args.end,
        args.quote,
        args.output,
        endpoint,
        args.quote_prices,
        max_requests=args.max_requests,
        requests_per_second=args.requests_per_second,
        cache_mb=args.cache_mb,
    )


if __name__ == "__main__":
    main()
