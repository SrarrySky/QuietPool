"""Volume strategy models."""

from __future__ import annotations

import json

import re

from decimal import Decimal

from typing import Any, Literal

from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


def canonical_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral_value():
        return format(normalized.quantize(Decimal(1)), "f")
    return format(normalized, "f")


def json_number_value(value: Decimal) -> int | float:
    """User-facing representation only; Monitoring wire body uses exact numeric text."""
    text = canonical_decimal(value)
    if "." not in text:
        return int(text)
    return float(text)


class MarketSnapshot(BaseModel):
    price_usdt: Decimal = Field(gt=0)
    pool_liquidity_usd: Decimal = Field(gt=0)
    observed_slippage_bps: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("10000"))
    organic_volume_1m_usd: Decimal = Field(default=Decimal("0"), ge=0)
    trend_return_bps: Decimal = Field(default=Decimal("0"), ge=Decimal("-10000"), le=Decimal("10000"))
    volatility_bps: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("10000"))
    available_wallet_count: int = Field(default=1, ge=1, le=1000)
    observed_at: int = Field(default=0, gt=0)
    source: Literal["MANUAL", "REPLAY", "EXTERNAL"] = "MANUAL"
    source_ref: str | None = Field(default=None, max_length=256)


class TradeSignal(BaseModel):
    strategy_id: str = Field(min_length=1, max_length=128)
    direction: Literal["BUY", "SELL"]
    scale: Decimal = Field(gt=0)
    window_ts: int = Field(ge=0)
    chain: Literal["SOLANA", "BNB"]
    token_address: str | None = Field(default=None, min_length=1, max_length=128)
    wallet_group_id: UUID | None = None
    bundle_flag: bool | None = None
    def public_payload(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "direction": self.direction,
            "scale": json_number_value(self.scale),
            "window_ts": self.window_ts,
            "chain": self.chain,
            "token_address": self.token_address,
            "wallet_group_id": str(self.wallet_group_id) if self.wallet_group_id else None,
            "bundle_flag": self.bundle_flag,
        }
    def canonical_monitoring_body(self) -> str:
        """Serialize scale as a JSON NUMBER, not a quoted Decimal string."""
        fields: list[str] = [
            '"strategy_id":' + json.dumps(self.strategy_id, ensure_ascii=False),
            '"direction":' + json.dumps(self.direction),
            '"scale":' + canonical_decimal(self.scale),
            '"window_ts":' + str(self.window_ts),
            '"chain":' + json.dumps(self.chain),
        ]
        if self.token_address is not None:
            fields.append('"token_address":' + json.dumps(self.token_address, ensure_ascii=False))
        if self.wallet_group_id is not None:
            fields.append('"wallet_group_id":' + json.dumps(str(self.wallet_group_id)))
        if self.bundle_flag is not None:
            fields.append('"bundle_flag":' + ("true" if self.bundle_flag else "false"))
        return "{" + ",".join(fields) + "}"


class MonitoringReceipt(BaseModel):
    receipt_id: str
    state: Literal["ACCEPTED", "REJECTED"]
    replayed: bool = False
    window_id: str
    reason_code: str | None = None

_BNB_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")

_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _is_32_byte_base58(value: str) -> bool:
    number = 0
    try:
        for char in value:
            number = number * 58 + _BASE58_ALPHABET.index(char)
    except ValueError:
        return False
    decoded = (
        b""
        if number == 0
        else number.to_bytes((number.bit_length() + 7) // 8, "big")
    )
    leading_zeroes = len(value) - len(value.lstrip("1"))
    decoded = (b"\x00" * leading_zeroes) + decoded
    return len(decoded) == 32


def _validate_formal_token_address(chain: str, token_address: str) -> None:
    token = token_address.strip()
    if chain == "BNB":
        if _BNB_ADDRESS.fullmatch(token) is None:
            raise ValueError("BNB token_address must be a 20-byte hex address (0x + 40 hex characters)")
        return
    if chain == "SOLANA":
        if not _is_32_byte_base58(token):
            raise ValueError("SOLANA token_address must be a 32-byte base58 public key")
        return
    raise ValueError("chain must be SOLANA or BNB")


class VolumeConfig(BaseModel):
    enabled: bool = True
    execution_mode: Literal["SIMULATION", "TESTNET"] = "SIMULATION"
    strategy_id: str = Field(default="volume-strategy", min_length=1, max_length=128)
    chain: Literal["SOLANA", "BNB"] = "SOLANA"
    token_address: str = Field(default="demo-token", min_length=1, max_length=128)
    wallet_group_id: UUID | None = None
    min_amount_usdt: Decimal = Field(default=Decimal("25"), gt=0)
    max_amount_usdt: Decimal = Field(default=Decimal("2000"), gt=0)
    bundle_threshold_usdt: Decimal = Field(default=Decimal("500"), gt=0)
    min_wallets_for_bundle: int = Field(default=2, ge=2, le=5)
    max_bundle_wallets: int = Field(default=5, ge=2, le=5)
    max_slippage_bps: Decimal = Field(default=Decimal("100"), gt=0, le=Decimal("2000"))
    max_pool_participation_bps: Decimal = Field(default=Decimal("50"), gt=0, le=Decimal("1000"))
    max_organic_volume_participation_bps: Decimal = Field(default=Decimal("1000"), gt=0, le=Decimal("10000"))
    slippage_sizing_factor: Decimal = Field(default=Decimal("0.25"), gt=0, le=1)
    market_max_age_sec: int = Field(default=0, ge=0, le=86400)
    max_future_skew_sec: int = Field(default=300, ge=0, le=3600)
    base_interval_sec: int = Field(default=30, ge=1, le=3600)
    min_interval_sec: int = Field(default=10, ge=1, le=3600)
    max_interval_sec: int = Field(default=120, ge=1, le=7200)
    max_signals_per_minute: int = Field(default=6, ge=1, le=60)
    # Self-trade guard window (seconds). 0 disables the controlled opposite-side
    # cross check; a positive value blocks a new controlled order that would cross
    # a recent opposite-side order from the same wallet group/token within this
    # window. Default off so the alternating wash-volume cycle keeps its behavior.
    self_trade_window_sec: int = Field(default=0, ge=0, le=3600)
    target_test_volume_per_min_usd: Decimal = Field(default=Decimal("1000"), ge=0)
    trend_threshold_bps: Decimal = Field(default=Decimal("20"), ge=0, le=Decimal("5000"))
    target_test_inventory_usd: Decimal = Field(default=Decimal("0"), ge=Decimal("-1000000"), le=Decimal("1000000"))
    inventory_rebalance_threshold_usd: Decimal = Field(default=Decimal("100"), ge=0)
    @field_validator("strategy_id", "token_address", mode="before")
    @classmethod
    def strip_text(cls, value):
        return str(value).strip()
    @model_validator(mode="after")
    def validate_ranges(self):
        if self.min_amount_usdt > self.max_amount_usdt:
            raise ValueError("min_amount_usdt must be <= max_amount_usdt")
        if self.min_interval_sec > self.base_interval_sec:
            raise ValueError("min_interval_sec must be <= base_interval_sec")
        if self.base_interval_sec > self.max_interval_sec:
            raise ValueError("base_interval_sec must be <= max_interval_sec")
        if self.min_wallets_for_bundle > self.max_bundle_wallets:
            raise ValueError("min_wallets_for_bundle must be <= max_bundle_wallets")
        return self
