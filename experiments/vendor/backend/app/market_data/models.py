"""Real dual-chain market-data models."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.compat.volume_strategy.models import MarketSnapshot


class Candle(BaseModel):
    ts: int
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    base_volume: Decimal = Field(ge=0)
    quote_volume_usd: Decimal = Field(ge=0)


class MarketDepth(BaseModel):
    midpoint: Decimal = Field(gt=0)
    best_bid: Decimal = Field(gt=0)
    best_ask: Decimal = Field(gt=0)
    depth_usd_1pct: Decimal = Field(gt=0)
    spread_bps: Decimal = Field(ge=0)


class RealMarketFetchRequest(BaseModel):
    chain: Literal["SOLANA", "BNB"]
    lookback_days: int = Field(default=60, ge=1, le=90)
    granularity_seconds: Literal[3600] = 3600


class RealMarketFetchResult(BaseModel):
    chain: Literal["SOLANA", "BNB"]
    provider: str
    symbol: str
    candle_count: int
    started_at: int
    ended_at: int
    snapshot: MarketSnapshot
