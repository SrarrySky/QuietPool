"""Volume strategy decision core.

Implements RiskManager, DirectionSelector, DynamicFrequencyController,
ExecutionModeSelector, LiquidityAwareSizer and ActivityController.
"""

from __future__ import annotations

from dataclasses import dataclass

from decimal import Decimal

from typing import Any

from app.compat.volume_strategy.models import (
    MarketSnapshot,
    VolumeConfig,
    TradeSignal,
    json_number_value,

)


class RiskLimitExceeded(Exception):
    pass


class NoSafeSize(Exception):
    pass


class StrategyDisabled(Exception):
    pass


class EmergencyStopActive(Exception):
    pass


class NotDueYet(Exception):
    def __init__(self, remaining: float):
        self.remaining = max(0.0, remaining)
        super().__init__(f"NOT_DUE_YET:{self.remaining:.3f}")


class PendingDeliveryExists(Exception):
    def __init__(self, signal_id: str):
        self.signal_id = signal_id
        super().__init__(f"pending/retryable delivery exists: {signal_id}")


class RiskManager:
    @staticmethod
    def assert_market(config: VolumeConfig, market: MarketSnapshot, now: float) -> None:
        if market.observed_slippage_bps >= config.max_slippage_bps:
            raise RiskLimitExceeded("observed slippage already exceeds configured maximum")
        if market.pool_liquidity_usd <= 0:
            raise RiskLimitExceeded("pool liquidity must be positive")
        if market.observed_at > now + config.max_future_skew_sec:
            raise RiskLimitExceeded("market snapshot timestamp is too far in the future")
        if config.market_max_age_sec > 0 and now - market.observed_at > config.market_max_age_sec:
            raise RiskLimitExceeded("market snapshot is stale")


@dataclass(frozen=True)


class StrategyState:
    rolling_volume_60s: Decimal = Decimal("0")
    test_inventory_usd: Decimal = Decimal("0")
    next_allowed_at: float = 0.0
    accepted_count_60s: int = 0
    accepted_signal_count: int = 0
    last_direction: str | None = None


@dataclass(frozen=True)


class StrategyDecision:
    direction: str
    scale: Decimal
    mode: str
    bundle_flag: bool
    requested_wallet_count: int
    recommended_interval_sec: int
    liquidity_cap_usd: Decimal
    slippage_cap_usd: Decimal
    organic_volume_cap_usd: Decimal | None
    test_volume_deficit_usd: Decimal
    constrained_by: str
    direction_reason: str
    funding_requirement_usd: Decimal = Decimal("0")
    slippage_cost_usd: Decimal = Decimal("0")
    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "scale": json_number_value(self.scale),
            "mode": self.mode,
            "bundle_flag": self.bundle_flag,
            "requested_wallet_count": self.requested_wallet_count,
            "recommended_interval_sec": self.recommended_interval_sec,
            "liquidity_cap_usd": json_number_value(self.liquidity_cap_usd),
            "slippage_cap_usd": json_number_value(self.slippage_cap_usd),
            "organic_volume_cap_usd": (
                json_number_value(self.organic_volume_cap_usd)
                if self.organic_volume_cap_usd is not None else None
            ),
            "test_volume_deficit_usd": json_number_value(self.test_volume_deficit_usd),
            "constrained_by": self.constrained_by,
            "direction_reason": self.direction_reason,
            "funding_requirement_usd": json_number_value(self.funding_requirement_usd),
            "slippage_cost_usd": json_number_value(self.slippage_cost_usd),
        }

_V4_SOFT_INVENTORY_LIMIT_USD = Decimal("1000")

_V4_HARD_INVENTORY_LIMIT_USD = Decimal("3000")

_V4_RECOVERY_GATE_USD = Decimal("700")

_V4_SOLANA_THRESHOLD_BPS = Decimal("20")

_V4_BNB_THRESHOLD_BPS = Decimal("5")


@dataclass(frozen=True)


class DirectionResult:
    direction: str
    reason: str
    signal_class: str = "FALLBACK"


class DirectionSelector:
    @staticmethod
    def _inventory_recovery(config: VolumeConfig, *, test_inventory_usd: Decimal) -> DirectionResult | None:
        gap = test_inventory_usd - config.target_test_inventory_usd
        if gap >= config.inventory_rebalance_threshold_usd:
            return DirectionResult("SELL", "v4_inventory_above_target", "INVENTORY_RECOVERY")
        if gap <= -config.inventory_rebalance_threshold_usd:
            return DirectionResult("BUY", "v4_inventory_below_target", "INVENTORY_RECOVERY")
        return None
    @staticmethod
    def _predictive(config: VolumeConfig, market: MarketSnapshot) -> DirectionResult | None:
        historical_return = market.trend_return_bps
        if config.chain == "SOLANA":
            if abs(historical_return) < _V4_SOLANA_THRESHOLD_BPS:
                return None
            return DirectionResult(
                "SELL" if historical_return > 0 else "BUY",
                "v4_predictive_mean_reversion",
                "PREDICTIVE",
            )
        if abs(historical_return) < _V4_BNB_THRESHOLD_BPS:
            return None
        return DirectionResult(
            "BUY" if historical_return > 0 else "SELL",
            "v4_predictive_momentum",
            "PREDICTIVE",
        )
    @staticmethod
    def select(config: VolumeConfig, market: MarketSnapshot, *, test_inventory_usd: Decimal, accepted_signal_count: int) -> DirectionResult:
        gap = test_inventory_usd - config.target_test_inventory_usd
        if abs(gap) >= _V4_RECOVERY_GATE_USD:
            recovery = DirectionSelector._inventory_recovery(config, test_inventory_usd=test_inventory_usd)
            if recovery is not None:
                return recovery
        predictive = DirectionSelector._predictive(config, market)
        if predictive is not None:
            return predictive
        recovery = DirectionSelector._inventory_recovery(config, test_inventory_usd=test_inventory_usd)
        if recovery is not None:
            return recovery
        return DirectionResult(
            "BUY" if accepted_signal_count % 2 == 0 else "SELL",
            "v4_no_edge_alternation",
            "FALLBACK",
        )


@dataclass(frozen=True)


class FrequencyResult:
    interval_sec: int
    trend_multiplier: float
    volatility_multiplier: float
    deficit_multiplier: float


class DynamicFrequencyController:
    @staticmethod
    def compute(config: VolumeConfig, market: MarketSnapshot, deficit_ratio: float) -> FrequencyResult:
        trend_strength = min(abs(float(market.trend_return_bps)) / 200.0, 1.0)
        trend_multiplier = 1.0 - 0.20 * trend_strength
        vol_strength = min(float(market.volatility_bps) / 300.0, 2.0)
        volatility_multiplier = 1.0 + 0.45 * vol_strength
        deficit_ratio = max(0.0, min(deficit_ratio, 1.0))
        deficit_multiplier = 1.0 - 0.25 * deficit_ratio
        interval = (
            config.base_interval_sec
            * trend_multiplier
            * volatility_multiplier
            * deficit_multiplier
        )
        interval = max(config.min_interval_sec, min(config.max_interval_sec, interval))
        return FrequencyResult(
            interval_sec=max(1, int(round(interval))),
            trend_multiplier=round(trend_multiplier, 4),
            volatility_multiplier=round(volatility_multiplier, 4),
            deficit_multiplier=round(deficit_multiplier, 4),
        )


@dataclass(frozen=True)


class ModeResult:
    mode: str
    bundle_flag: bool
    requested_wallet_count: int


class ExecutionModeSelector:
    @staticmethod
    def select(config: VolumeConfig, market: MarketSnapshot, scale_usd: Decimal) -> ModeResult:
        import math
        if (
            scale_usd >= config.bundle_threshold_usdt
            and market.available_wallet_count >= config.min_wallets_for_bundle
        ):
            ratio = float(scale_usd / config.bundle_threshold_usdt)
            requested = max(config.min_wallets_for_bundle, int(math.ceil(ratio)))
            requested = min(requested, config.max_bundle_wallets, market.available_wallet_count, 5)
            return ModeResult("BUNDLE_INTENT", True, requested)
        return ModeResult("NORMAL", False, 1)


@dataclass(frozen=True)


class SizingResult:
    scale_usd: Decimal
    liquidity_cap_usd: Decimal
    slippage_cap_usd: Decimal
    organic_volume_cap_usd: Decimal | None
    test_volume_cap_usd: Decimal | None
    constrained_by: str


class LiquidityAwareSizer:
    @staticmethod
    def compute(config: VolumeConfig, market: MarketSnapshot, test_volume_deficit_usd: Decimal) -> SizingResult:
        ten_k = Decimal("10000")
        liquidity_cap = market.pool_liquidity_usd * config.max_pool_participation_bps / ten_k
        remaining_slippage = max(Decimal("0"), config.max_slippage_bps - market.observed_slippage_bps)
        slippage_cap = (
            market.pool_liquidity_usd * remaining_slippage / ten_k * config.slippage_sizing_factor
        )
        organic_cap = None
        if market.organic_volume_1m_usd > 0:
            organic_cap = market.organic_volume_1m_usd * config.max_organic_volume_participation_bps / ten_k
        test_cap = None
        if config.target_test_volume_per_min_usd > 0:
            test_cap = max(Decimal("0"), test_volume_deficit_usd)
        candidates: dict[str, Decimal] = {
            "config_max": config.max_amount_usdt,
            "liquidity_cap": liquidity_cap,
            "slippage_cap": slippage_cap,
        }
        if organic_cap is not None:
            candidates["organic_volume_cap"] = organic_cap
        if test_cap is not None:
            candidates["test_volume_cap"] = test_cap
        constrained_by, raw = min(candidates.items(), key=lambda item: item[1])
        scale = Decimal("0") if raw < config.min_amount_usdt else raw.quantize(Decimal("0.01"))
        return SizingResult(
            scale_usd=scale,
            liquidity_cap_usd=liquidity_cap.quantize(Decimal("0.01")),
            slippage_cap_usd=slippage_cap.quantize(Decimal("0.01")),
            organic_volume_cap_usd=(organic_cap.quantize(Decimal("0.01")) if organic_cap is not None else None),
            test_volume_cap_usd=(test_cap.quantize(Decimal("0.01")) if test_cap is not None else None),
            constrained_by=constrained_by,
        )


class ActivityController:
    def preview(
        self,
        config: VolumeConfig,
        market: MarketSnapshot,
        state: StrategyState,
        *,
        now: float,
        enforce_due: bool = False,
    ) -> StrategyDecision:
        RiskManager.assert_market(config, market, now)
        if enforce_due and now < state.next_allowed_at:
            raise NotDueYet(state.next_allowed_at - now)
        if enforce_due and state.accepted_count_60s >= config.max_signals_per_minute:
            raise RiskLimitExceeded(
                f"max {config.max_signals_per_minute} accepted signals/minute exceeded"
            )
        deficit = max(
            Decimal("0"),
            config.target_test_volume_per_min_usd - state.rolling_volume_60s,
        )
        deficit_ratio = (
            float(deficit / config.target_test_volume_per_min_usd)
            if config.target_test_volume_per_min_usd > 0
            else 0.0
        )
        sizing = LiquidityAwareSizer.compute(config, market, deficit)
        direction = DirectionSelector.select(
            config,
            market,
            test_inventory_usd=state.test_inventory_usd,
            accepted_signal_count=state.accepted_signal_count,
        )
        gap = state.test_inventory_usd - config.target_test_inventory_usd
        abs_gap = abs(gap)
        direction_value = direction.direction
        direction_reason = direction.reason
        scale = sizing.scale_usd
        projected_gap = gap + (scale if direction_value == "BUY" else -scale)
        expands_inventory = abs(projected_gap) > abs_gap
        crosses_hard_limit = (
            abs(projected_gap) > _V4_HARD_INVENTORY_LIMIT_USD and abs(projected_gap) > abs_gap
        )
        if expands_inventory and (abs_gap >= _V4_HARD_INVENTORY_LIMIT_USD or crosses_hard_limit):
            direction_value = "SELL" if gap > 0 else "BUY"
            direction_reason = "v4_hard_inventory_recovery"
            expands_inventory = False
        projected_gap = gap + (scale if direction_value == "BUY" else -scale)
        crosses_soft_limit = (
            abs(projected_gap) > _V4_SOFT_INVENTORY_LIMIT_USD and abs(projected_gap) > abs_gap
        )
        if expands_inventory and (abs_gap >= _V4_SOFT_INVENTORY_LIMIT_USD or crosses_soft_limit):
            if gap == 0:
                scale = min(scale, _V4_SOFT_INVENTORY_LIMIT_USD).quantize(Decimal("0.01"))
                direction_reason = f"{direction_reason}:v4_soft_headroom_clamp"
            else:
                direction_value = "SELL" if gap > 0 else "BUY"
                direction_reason = "v4_soft_inventory_recovery"
                scale = min(scale, _V4_SOFT_INVENTORY_LIMIT_USD + abs_gap).quantize(Decimal("0.01"))
        if scale < config.min_amount_usdt:
            raise NoSafeSize("V4 inventory guard reduced scale below min_amount_usdt")
        mode = ExecutionModeSelector.select(config, market, scale)
        frequency = DynamicFrequencyController.compute(config, market, deficit_ratio)
        # Funding requirement: notional + worst-case slippage cost, sized against
        # the configured max slippage. This is the capital estimate needed to
        # cover the intended order before bundle build (任务一 资金需求测算).
        slippage_cost_usd = (scale * config.max_slippage_bps / Decimal("10000")).quantize(Decimal("0.01"))
        funding_requirement_usd = (scale + slippage_cost_usd).quantize(Decimal("0.01"))
        return StrategyDecision(
            direction=direction_value,
            scale=scale,
            mode=mode.mode,
            bundle_flag=mode.bundle_flag,
            requested_wallet_count=mode.requested_wallet_count,
            recommended_interval_sec=frequency.interval_sec,
            liquidity_cap_usd=sizing.liquidity_cap_usd,
            slippage_cap_usd=sizing.slippage_cap_usd,
            organic_volume_cap_usd=sizing.organic_volume_cap_usd,
            test_volume_deficit_usd=deficit.quantize(Decimal("0.01")),
            constrained_by=sizing.constrained_by,
            direction_reason=direction_reason,
            funding_requirement_usd=funding_requirement_usd,
            slippage_cost_usd=slippage_cost_usd,
        )
    def prepare_signal(
        self,
        config: VolumeConfig,
        market: MarketSnapshot,
        state: StrategyState,
        *,
        now: float,
    ) -> tuple[TradeSignal, StrategyDecision]:
        decision = self.preview(config, market, state, now=now, enforce_due=True)
        signal = TradeSignal(
            strategy_id=config.strategy_id,
            direction=decision.direction,
            scale=decision.scale,
            window_ts=int(now),
            chain=config.chain,
            token_address=config.token_address,
            wallet_group_id=config.wallet_group_id,
            bundle_flag=decision.bundle_flag,
        )
        return signal, decision
