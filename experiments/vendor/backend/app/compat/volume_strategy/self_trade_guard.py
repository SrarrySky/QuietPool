"""Self-trade guard.

Blocks controlled opposite-side orders targeting the same token within the same
time window, so a wash-volume cycle cannot accidentally trade against itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class IntendedOrder:
    owner_group: str
    token_address: str
    direction: Literal["BUY", "SELL"]
    notional_usd: float
    window_ts: int


class SelfTradeRisk(ValueError):
    pass


class SelfTradeGuard:
    """Blocks controlled opposite-side orders in the same token/time window."""

    @staticmethod
    def assert_no_controlled_cross(
        orders: list[IntendedOrder], *, window_seconds: int = 60
    ) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        for i, a in enumerate(orders):
            for b in orders[i + 1:]:
                if (
                    a.owner_group == b.owner_group
                    and a.token_address == b.token_address
                    and abs(a.window_ts - b.window_ts) < window_seconds
                    and a.direction != b.direction
                ):
                    raise SelfTradeRisk(
                        "controlled opposite-side orders detected in the same token/time window"
                    )
