"""Call the frozen strategy; only sandbox fills and bookkeeping are new."""

from collections import deque
from contextlib import ExitStack
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

import numpy as np
from app.compat.volume_strategy import strategy
from app.compat.volume_strategy.bundle_plan import BundlePlanner, PlannedLeg
from app.compat.volume_strategy.models import MarketSnapshot, VolumeConfig
from app.compat.volume_strategy.self_trade_guard import (
    IntendedOrder,
    SelfTradeGuard,
    SelfTradeRisk,
)

# Explicit per-spec defaults. Each spec declares the vendor controls it deviates
# from the honest baseline instead of stacking levels, so effects stay
# attributable one-factor-at-a-time. Legacy cumulative ladder entries are
# translated into this same schema below.
_SPEC_DEFAULTS = dict(
    name="",
    target=None,
    level=None,
    max_amount_usdt=2000,
    max_organic_volume_participation_bps=1000,
    min_interval_sec=10,
    base_interval_sec=30,
    max_interval_sec=120,
    available_wallet_count=1,
    bundle_threshold_usdt=500,
    guard=0,
    band=None,
    target_test_inventory_usd=0,
    trend_input=False,
    organic_input=False,
    random_funding=False,
)


def _expand(spec):
    """Return a spec dict with every explicit field defaulted in place."""
    filled = dict(_SPEC_DEFAULTS)
    filled.update(spec)
    return filled


def ladder_grid(settings):
    """Expand ladder settings into the full ordered list of strategy specs.

    When "settings" carries an explicit "specs" list (the directed Top Player
    grid), each entry is returned with defaults applied. Otherwise the legacy
    cumulative M0--M7 ladder is expanded and translated into the same explicit
    schema so the manipulator no longer branches on a "level" integer.

    Args:
        settings: Ladder settings mapping. Either {"specs": [...]} or the
            legacy keys "wallets", "participation_bps", "guard_seconds",
            "inventory_bands", and "m7_definition".

    Returns:
        List of explicit spec dicts.
    """
    if "specs" in settings:
        return [_expand(spec) for spec in settings["specs"]]

    wallets = settings["wallets"]
    grid = []
    for i in range(5):
        spec = _expand(dict(name=f"M{i}", level=i))
        if i == 0:
            spec.update(
                min_interval_sec=30, base_interval_sec=30, max_interval_sec=30
            )
        if i >= 2:
            spec["trend_input"] = True
        if i >= 3:
            spec["available_wallet_count"] = wallets
        if i >= 4:
            spec["random_funding"] = True
        grid.append(spec)
    for a in settings["participation_bps"]:
        spec = _expand(
            dict(
                name=f"M5_a{a}",
                level=5,
                max_organic_volume_participation_bps=a,
                trend_input=True,
                organic_input=True,
                available_wallet_count=wallets,
                random_funding=True,
            )
        )
        grid.append(spec)
    for g in settings["guard_seconds"]:
        spec = _expand(
            dict(
                name=f"M6_t{g}",
                level=6,
                guard=g,
                trend_input=True,
                organic_input=True,
                available_wallet_count=wallets,
                random_funding=True,
            )
        )
        grid.append(spec)
    # M7 is S1: branch from M5 at 10%, not an additional guard on M6.
    for s, h in settings["inventory_bands"]:
        spec = _expand(
            dict(
                name=f"M7_i{s}_{h}",
                level=7,
                band=[s, h],
                trend_input=True,
                organic_input=True,
                available_wallet_count=wallets,
                random_funding=True,
            )
        )
        grid.append(spec)
    return grid


class Manipulator:
    """Wraps the frozen volume strategy with sandbox-only bookkeeping.

    The strategy itself is unchanged; this class only adapts its inputs to the
    replay sandbox and records fills, decisions, and nominal state separately
    from physical inventory.
    """

    def __init__(self, spec, wallet_count=5):
        self.spec = spec
        self.wallets = [f"w{i}" for i in range(wallet_count)]
        self.guard = spec.get("guard", 0)
        self.band = spec.get("band")
        self.trend_input = spec.get("trend_input", False)
        self.organic_input = spec.get("organic_input", False)
        self.available_wallet_count = min(
            spec.get("available_wallet_count", 1), len(self.wallets)
        )
        options = dict(
            chain="BNB",
            execution_mode="SIMULATION",
            self_trade_window_sec=self.guard,
            max_organic_volume_participation_bps=spec.get(
                "max_organic_volume_participation_bps", spec.get("alpha", 1000)
            ),
            min_interval_sec=spec.get("min_interval_sec", 10),
            base_interval_sec=spec.get("base_interval_sec", 30),
            max_interval_sec=spec.get("max_interval_sec", 120),
            max_amount_usdt=spec.get("max_amount_usdt", 2000),
            bundle_threshold_usdt=spec.get("bundle_threshold_usdt", 500),
            target_test_inventory_usd=spec.get("target_test_inventory_usd", 0),
        )
        self.config = VolumeConfig(**options)
        self.state = strategy.StrategyState()
        self.events = deque()
        self.orders = deque()
        self.decisions = []

    def act(self, pool, ts, quote_usd, organic_volume, prices, balances):
        """Advance the strategy one block and apply any resulting fills.

        Args:
            pool: :class:`CpmmPool` being traded.
            ts: Block timestamp in seconds.
            quote_usd: USD price of one quote unit.
            organic_volume: Organic volume in the trailing 60-second window.
            prices: Deque of "(timestamp, price)" price history.
            balances: Mutable "[quote, token]" physical balances.

        Returns:
            List of :class:`SwapFill` objects executed this block.
        """
        while self.events and self.events[0][0] <= ts - 60:
            self.events.popleft()
        while self.orders and self.orders[0].window_ts <= ts - max(1, self.guard):
            self.orders.popleft()
        self.state = replace(
            self.state,
            rolling_volume_60s=sum((v for _, v in self.events), Decimal(0)),
            accepted_count_60s=len(self.events),
        )
        if ts < self.state.next_allowed_at:
            return []
        recent = [(t, p) for t, p in prices if t >= ts - 1800]
        past = [p for t, p in prices if t <= ts - 300]
        trend = np.log(pool.price() / past[-1]) * 10000 if past else 0.0
        minute = {}
        for t, p in recent:
            minute[t // 60] = p
        returns = np.diff(np.log(list(minute.values())))
        volatility = float(np.std(returns)) * 10000 if len(returns) > 1 else 0.0
        market = MarketSnapshot(
            price_usdt=str(pool.price() * quote_usd),
            pool_liquidity_usd=str(
                2 * pool.reserve_quote_raw / 10**pool.quote_decimals * quote_usd
            ),
            organic_volume_1m_usd=str(organic_volume if self.organic_input else 0),
            trend_return_bps=str(
                float(np.clip(trend, -10000, 10000)) if self.trend_input else 0
            ),
            volatility_bps=str(min(10000, volatility)),
            available_wallet_count=self.available_wallet_count,
            observed_at=ts,
            source="REPLAY",
        )
        try:
            with ExitStack() as stack:
                if self.band:
                    for name, value in zip(
                        (
                            "_V4_SOFT_INVENTORY_LIMIT_USD",
                            "_V4_HARD_INVENTORY_LIMIT_USD",
                        ),
                        self.band,
                        strict=False,
                    ):
                        stack.enter_context(
                            patch.object(strategy, name, Decimal(value))
                        )
                decision = strategy.ActivityController().preview(
                    self.config, market, self.state, now=ts, enforce_due=True
                )
            order = IntendedOrder(
                "controlled",
                self.config.token_address,
                decision.direction,
                float(decision.scale),
                ts,
            )
            if self.guard:
                SelfTradeGuard.assert_no_controlled_cross(
                    [*self.orders, order], window_seconds=self.guard
                )
        except (
            strategy.NotDueYet,
            strategy.RiskLimitExceeded,
            strategy.NoSafeSize,
            SelfTradeRisk,
        ) as exc:
            self.decisions.append(dict(ts=ts, status=type(exc).__name__))
            return []
        if decision.bundle_flag:
            legs = BundlePlanner.build(
                chain="BNB",
                token_address=self.config.token_address,
                direction=decision.direction,
                scale_usd=decision.scale,
                wallet_refs=self.wallets[: decision.requested_wallet_count],
                atomic_requested=False,
                purpose="SIMULATION",
            ).legs
        else:
            legs = [PlannedLeg(self.wallets[0], decision.direction, decision.scale)]
        fills = []
        for leg in legs:
            unit = quote_usd if leg.direction == "BUY" else pool.price() * quote_usd
            decimals = (
                pool.quote_decimals if leg.direction == "BUY" else pool.token_decimals
            )
            raw = int(leg.notional_usd / Decimal(str(unit)) * 10**decimals)
            # Endowments are consolidated; funding edges are attribution-only.
            available = balances[0] if leg.direction == "BUY" else balances[1]
            if raw / 10**decimals > available:
                self.decisions.append(dict(ts=ts, status="InsufficientEndowment"))
                continue
            fill = pool.swap_exact_in(
                side=leg.direction,
                amount_in_raw=raw,
                ts=ts,
                actor="manip",
                wallet=leg.wallet_ref,
                quote_usd=quote_usd,
            )
            if fill:
                balances[0] += fill.quote_flow
                balances[1] += fill.signed_token
                fills.append(fill)
        if fills:
            actual = sum(Decimal(str(f.notional_usd)) for f in fills)
            self.events.append((ts, actual))
            self.orders.append(order)
            # Preserve the nominal state, separate from physical inventory.
            signed = decision.scale if decision.direction == "BUY" else -decision.scale
            self.state = strategy.StrategyState(
                sum((v for _, v in self.events), Decimal(0)),
                self.state.test_inventory_usd + signed,
                ts + decision.recommended_interval_sec,
                len(self.events),
                self.state.accepted_signal_count + 1,
                decision.direction,
            )
            self.decisions.append(dict(ts=ts, status="Filled", **decision.to_dict()))
        return fills
