"""Bundle plan builder.

Produces an offline, authorization-neutral bundle plan that splits a target
notional across up to five wallet references. Settlement owns the actual
authorization / build / sign / submit; Volume only emits the intent.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal


@dataclass(frozen=True)
class PlannedLeg:
    wallet_ref: str
    direction: Literal["BUY", "SELL"]
    notional_usd: Decimal


@dataclass(frozen=True)
class BundlePlan:
    chain: Literal["SOLANA", "BNB"]
    token_address: str
    legs: tuple[PlannedLeg, ...]
    atomic_requested: bool
    purpose: Literal["SIMULATION", "TESTNET_VALIDATION"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "token_address": self.token_address,
            "legs": [
                {
                    "wallet_ref": x.wallet_ref,
                    "direction": x.direction,
                    "notional_usd": float(x.notional_usd),
                }
                for x in self.legs
            ],
            "atomic_requested": self.atomic_requested,
            "purpose": self.purpose,
        }


class BundlePlanError(ValueError):
    pass


class BundlePlanner:
    """Offline plan only. Settlement owns actual authorization/build/sign/submit."""

    @staticmethod
    def build(
        *,
        chain: str,
        token_address: str,
        direction: str,
        scale_usd: Decimal,
        wallet_refs: list[str],
        atomic_requested: bool,
        purpose: str,
    ) -> BundlePlan:
        if purpose not in {"SIMULATION", "TESTNET_VALIDATION"}:
            raise BundlePlanError("only simulation/testnet validation plans are supported")
        if chain not in {"SOLANA", "BNB"}:
            raise BundlePlanError("unsupported chain")
        if direction not in {"BUY", "SELL"}:
            raise BundlePlanError("unsupported direction")
        if scale_usd <= 0:
            raise BundlePlanError("scale_usd must be positive")
        if not wallet_refs:
            raise BundlePlanError("at least one wallet reference is required")

        selected = tuple(wallet_refs[:5])
        total_cents = int((scale_usd * 100).quantize(Decimal("1")))
        each, remainder = divmod(total_cents, len(selected))
        legs = tuple(
            PlannedLeg(
                wallet_ref=wallet,
                direction=direction,
                notional_usd=Decimal(each + (1 if i < remainder else 0)) / 100,
            )
            for i, wallet in enumerate(selected)
        )
        return BundlePlan(chain, token_address, legs, atomic_requested, purpose)
