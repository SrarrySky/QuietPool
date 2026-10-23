from __future__ import annotations

from decimal import Decimal

import httpx

from app.market_data.models import Candle, MarketDepth


class BinanceBnbMarketProvider:
    """Public read-only BNBUSDT market-data adapter.

    Uses Binance's market-data-only domain; no API key and no order endpoint.
    """

    base_url = "https://data-api.binance.vision"
    symbol = "BNBUSDT"

    def __init__(self, *, timeout_seconds: float = 10.0, transport=None):
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
                headers={"User-Agent": "regtech-market-data"},
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def candles(self, start_ts: int, end_ts: int) -> list[Candle]:
        all_rows: dict[int, Candle] = {}
        client = self._get_client()
        cursor_ms = start_ts * 1000
        end_ms = end_ts * 1000
        while cursor_ms < end_ms:
            response = client.get(
                "/api/v3/klines",
                params={
                    "symbol": self.symbol,
                    "interval": "1h",
                    "startTime": cursor_ms,
                    "endTime": end_ms,
                    "limit": 1000,
                },
            )
            response.raise_for_status()
            rows = response.json()
            if not rows:
                break
            for row in rows:
                # open_time, open, high, low, close, volume, close_time,
                # quote_asset_volume, ...
                ts = int(row[0]) // 1000
                all_rows[ts] = Candle(
                    ts=ts,
                    open=Decimal(str(row[1])),
                    high=Decimal(str(row[2])),
                    low=Decimal(str(row[3])),
                    close=Decimal(str(row[4])),
                    base_volume=Decimal(str(row[5])),
                    quote_volume_usd=Decimal(str(row[7])),
                )
            next_ms = int(rows[-1][0]) + 3600_000
            if next_ms <= cursor_ms:
                break
            cursor_ms = next_ms
        return sorted(all_rows.values(), key=lambda x: x.ts)

    def depth(self) -> MarketDepth:
        client = self._get_client()
        response = client.get(
            "/api/v3/depth", params={"symbol": self.symbol, "limit": 1000}
        )
        response.raise_for_status()
        body = response.json()

        bids = [(Decimal(str(p)), Decimal(str(q))) for p, q in body["bids"]]
        asks = [(Decimal(str(p)), Decimal(str(q))) for p, q in body["asks"]]
        if not bids or not asks:
            raise ValueError(f"Binance depth returned an empty order book for {self.symbol}")
        best_bid, best_ask = bids[0][0], asks[0][0]
        midpoint = (best_bid + best_ask) / Decimal("2")
        lower = midpoint * Decimal("0.99")
        upper = midpoint * Decimal("1.01")
        depth_usd = sum((p * q for p, q in bids if p >= lower), Decimal("0"))
        depth_usd += sum((p * q for p, q in asks if p <= upper), Decimal("0"))
        spread_bps = ((best_ask - best_bid) / midpoint) * Decimal("10000")
        return MarketDepth(
            midpoint=midpoint,
            best_bid=best_bid,
            best_ask=best_ask,
            depth_usd_1pct=max(depth_usd, Decimal("1")),
            spread_bps=max(spread_bps, Decimal("0")),
        )
