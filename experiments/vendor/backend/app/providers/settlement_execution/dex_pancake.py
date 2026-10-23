"""PancakeSwap V2/V3 quote + unsigned EVM build (V4.1B, BSC mainnet).

Scope: PancakeSwap **V2 and V3 only** (Leader's required scope). Infinity /
Smart-Router-equivalent multi-hop best-route is NOT claimed. Python EVM boundary
built directly against official contract ABIs; no Node/TypeScript runtime is
introduced.

Official BSC mainnet contract addresses (reviewable constants; source:
PancakeSwap official docs / BscScan, re-verified 2026-08-24):

- V2 Factory      0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73
- V2 Router       0x10ED43C718714eb63d5aA57B78B54704E256024E
- V3 Factory      0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865
- V3 SwapRouter   0x1b81D678ffb9C0263b24A97847620C99d213eB14
- V3 QuoterV2     0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997
- Universal Router 0x1A0A18AC4BECDDbd6389559687d1A73d8927E416

Semantics:

- V2 quote evidence is backed by ACTUAL pair reserves (``getReserves()``) and the
  router's 9975/10000 fee math (0.25%) — never invented.
- V3 quote evidence uses the official ``QuoterV2.quoteExactInput`` across the
  configured fee tiers; only real returned values are parsed. An unavailable
  pool is skipped; a malformed quote is rejected (DEX_INVALID_RESPONSE).
- ``minimum_amount_out`` = integer floor(expected * (10000 - slippage_bps) / 10000),
  never float, never ``expected`` when slippage > 0, never ``0`` to "make it easy".
- ``liquidity_depth`` is never invented. V2 records PAIR_RESERVES evidence with
  explicit units; V3 records no universal USD depth (the quote scalar is not
  liquidity depth).
- Builder tip is NOT mixed into router calldata (48 Club builder tip remains a
  separate EVM-building concern from V4.1A).
- Approval: ERC-20 allowance is queried and modeled; no fake Permit2 signature
  is ever generated; unknown allowance means the swap is not pretended to be
  executable. Native ``BNB`` input requires no approval (token→token only in
  this slice; native routing is a documented boundary).
- Output is ALWAYS UNSIGNED (chain_id 56, no private key, no signature).
"""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any

import httpx
from eth_abi import decode, encode
from eth_utils import keccak

from app.providers.settlement_execution.dex import (
    ApprovalRequirement,
    DexBuildResult,
    DexTransactionBuilder,
    LiquidityEvidence,
    QuoteEvidence,
    RouteLegEvidence,
    UnsignedEvmTransaction,
)
from app.providers.settlement_execution.errors import (
    SettlementDexBuildFailedError,
    SettlementDexInvalidResponseError,
    SettlementDexQuoteUnavailableError,
    SettlementDexRouteUnavailableError,
    SettlementDexSlippageInvalidError,
)

CHAIN_ID_BSC = 56
BSC_NATIVE = "BNB"  # sentinel for native BNB input
BSC_WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
BSC_USDT = "0x55d398326f99059fF775485246999027B3197955"  # reference stable in (not auto-selected)

# BSC testnet identity (V4.2A sandbox qualification). Production BNB path stays
# chain_id 56; the verifier compares against the expected configured network and
# a testnet transaction never passes production mainnet verification.
BSC_TESTNET_CHAIN_ID = 97

# Official PancakeSwap BSC TESTNET contract addresses (reviewed 2026-08-24
# against developer.pancakeswap.finance/contracts/v3/addresses and the
# PancakeSwap V2 testnet documentation). These are a SEPARATE reviewed set — do
# NOT reuse mainnet constants just because some contracts share an address.
PANCAKE_TESTNET_CONTRACTS: dict[str, Any] = {
    "chain_id": BSC_TESTNET_CHAIN_ID,
    "v3_factory": "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",  # shared address per official docs
    "v3_swap_router": "0x1b81D678ffb9C0263b24A97847620C99d213eB14",
    "v3_quoter_v2": "0xbC203d7f83677c7ed3F7acEc959963E7F4ECC5C2",
    # V2 BSC testnet Factory/Router (V4.2A.1 correction 2026-08-24).
    "v2_factory": "0x6725F303b657a9451d8BA641348b6761A6CC7a17",
    "v2_router": "0xD99D1c33F9fC3444f8101754aBC46c52416550D1",
}

# V4.2A.1: V2 testnet availability semantics are precise. PANCAKE_V2_TESTNET_AVAILABLE
# means ONLY that a reviewed testnet address SET exists — it does NOT prove a
# liquid test route (see V2_TESTNET_LIQUID_ROUTE_VERIFIED below).
PANCAKE_V2_TESTNET_AVAILABLE = True
PANCAKE_V3_TESTNET_AVAILABLE = True
V2_TESTNET_ADDRESS_DOCUMENTED = True
V2_TESTNET_CONTRACT_CODE_VERIFIED = True
V2_TESTNET_LIQUID_ROUTE_VERIFIED = "NOT_ATTEMPTED"

PANCAKE_CONTRACTS: dict[str, Any] = {
    "chain_id": CHAIN_ID_BSC,
    "v2_factory": "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
    "v2_router": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
    "v3_factory": "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",
    "v3_swap_router": "0x1b81D678ffb9C0263b24A97847620C99d213eB14",
    "v3_quoter_v2": "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997",
    "universal_router": "0x1A0A18AC4BECDDbd6389559687d1A73d8927E416",
}

# Pancake V3 fee tiers (official: 100 = 0.01%, 500 = 0.05%, 2500 = 0.25%, 10000 = 1%).
V3_FEE_TIERS: tuple[int, ...] = (100, 500, 2500, 10000)


# ---------------------------------------------------------------------------
# ABI helpers (eth_abi / eth_utils; independent of the frozen evm_abi.py)
# ---------------------------------------------------------------------------
def _selector(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def _encode_call(signature: str, types: list[str], args: list[Any]) -> str:
    data = _selector(signature) + encode(types, args)
    return "0x" + data.hex()


def _decode_result(types: list[str], raw_hex: str) -> tuple[Any, ...]:
    try:
        raw = bytes.fromhex(raw_hex[2:] if raw_hex.startswith("0x") else raw_hex)
        return decode(types, raw)
    except ValueError as exc:
        raise SettlementDexInvalidResponseError(
            "EVM response is not valid hex", details={"reason": "INVALID_HEX"}
        )
    except Exception as exc:
        raise SettlementDexInvalidResponseError(
            "EVM response could not be decoded",
            details={"reason": "ABI_DECODE_FAILED"},
        )


def _pack_v3_path(token_in: str, fee: int, token_out: str) -> bytes:
    return bytes.fromhex(token_in[2:]) + fee.to_bytes(3, "big") + bytes.fromhex(token_out[2:])


def quote_v2_exact_in(amount_in: int, reserve_in: int, reserve_out: int) -> int | None:
    """PancakeSwap Exchange V2 exact-in constant-product quote.

    Current PancakeSwap V2 uses a fixed **0.25%** trading fee, so the fee
    multiplier is **9975 / 10000** (NOT the older 997 / 1000 = 0.30%).

    Integer arithmetic only::

        amount_in_with_fee = amount_in * 9975
        numerator         = amount_in_with_fee * reserve_out
        denominator       = reserve_in * 10000 + amount_in_with_fee
        amount_out        = numerator // denominator

    Inputs must satisfy amount_in > 0, reserve_in > 0, reserve_out > 0,
    otherwise the route is unavailable (returns None).
    """
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return None
    amount_in_with_fee = amount_in * 9975
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in * 10000 + amount_in_with_fee
    if denominator == 0:
        return None
    return numerator // denominator


def slippage_min_out(expected_raw: int, slippage_bps: int) -> int:
    """Integer floor: expected * (10000 - slippage_bps) / 10000. Never float."""
    return (expected_raw * (10000 - slippage_bps)) // 10000


# ---------------------------------------------------------------------------
# EVM RPC client boundary (injectable; no live calls in unit tests)
# ---------------------------------------------------------------------------
class EvmRpcClient(ABC):
    @abstractmethod
    def is_configured(self) -> bool:
        ...

    @abstractmethod
    def eth_call(self, *, to: str, data: str) -> str:
        """Return the raw 0x-hex result of an ``eth_call``. Must raise only
        controlled sanitized errors (never the RPC URL / credentials / body)."""

    # --- read-only helpers (V4.1C live-read verification) --------------------
    # Defaults fail closed so pre-V4.1C test fakes remain valid.
    def chain_id(self) -> int:
        raise SettlementDexQuoteUnavailableError(
            "chain_id read is not supported by this RPC client",
            details={"reason": "UNSUPPORTED_READ"},
        )

    def get_code(self, address: str) -> str:
        raise SettlementDexQuoteUnavailableError(
            "eth_getCode read is not supported by this RPC client",
            details={"reason": "UNSUPPORTED_READ"},
        )

    def get_transaction_count(self, address: str, block: str = "pending") -> int:
        raise SettlementDexQuoteUnavailableError(
            "eth_getTransactionCount read is not supported by this RPC client",
            details={"reason": "UNSUPPORTED_READ"},
        )

    def gas_price(self) -> int:
        raise SettlementDexQuoteUnavailableError(
            "eth_gasPrice read is not supported by this RPC client",
            details={"reason": "UNSUPPORTED_READ"},
        )


class HttpxEvmRpcClient(EvmRpcClient):
    def __init__(self, rpc_url: str = "", timeout_seconds: float = 10.0) -> None:
        self._rpc_url = rpc_url.rstrip("/")
        self._timeout = timeout_seconds

    def is_configured(self) -> bool:
        return bool(self._rpc_url)

    def eth_call(self, *, to: str, data: str) -> str:
        if not self._rpc_url:
            raise SettlementDexQuoteUnavailableError(
                "BNB RPC is not configured", details={"reason": "RPC_NOT_CONFIGURED"}
            )
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [{"to": to, "data": data}, "latest"],
        }
        try:
            resp = httpx.post(self._rpc_url, json=payload, timeout=self._timeout)
        except httpx.TimeoutException:
            raise SettlementDexQuoteUnavailableError(
                "BNB RPC call timed out", details={"reason": "RPC_TIMEOUT"}
            )
        except httpx.HTTPError:
            raise SettlementDexQuoteUnavailableError(
                "BNB RPC request failed", details={"reason": "RPC_REQUEST_FAILED"}
            )
        if resp.status_code >= 400:
            raise SettlementDexQuoteUnavailableError(
                "BNB RPC returned an error status", details={"reason": "RPC_HTTP_ERROR"}
            )
        try:
            body = resp.json()
        except ValueError:
            raise SettlementDexQuoteUnavailableError(
                "BNB RPC returned a non-JSON response", details={"reason": "RPC_BAD_JSON"}
            )
        if body.get("error"):
            raise SettlementDexQuoteUnavailableError(
                "BNB RPC call reverted", details={"reason": "RPC_REVERT"}
            )
        result = body.get("result")
        if not result:
            raise SettlementDexQuoteUnavailableError(
                "BNB RPC returned no result", details={"reason": "RPC_EMPTY_RESULT"}
            )
        return str(result)

    def _jsonrpc(self, method: str, params: list[Any]) -> Any:
        if not self._rpc_url:
            raise SettlementDexQuoteUnavailableError(
                "BNB RPC is not configured", details={"reason": "RPC_NOT_CONFIGURED"}
            )
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        try:
            resp = httpx.post(self._rpc_url, json=payload, timeout=self._timeout)
        except httpx.TimeoutException:
            raise SettlementDexQuoteUnavailableError(
                "BNB RPC call timed out", details={"reason": "RPC_TIMEOUT"}
            )
        except httpx.HTTPError:
            raise SettlementDexQuoteUnavailableError(
                "BNB RPC request failed", details={"reason": "RPC_REQUEST_FAILED"}
            )
        if resp.status_code >= 400:
            raise SettlementDexQuoteUnavailableError(
                "BNB RPC returned an error status", details={"reason": "RPC_HTTP_ERROR"}
            )
        try:
            body = resp.json()
        except ValueError:
            raise SettlementDexQuoteUnavailableError(
                "BNB RPC returned a non-JSON response", details={"reason": "RPC_BAD_JSON"}
            )
        if body.get("error"):
            raise SettlementDexQuoteUnavailableError(
                "BNB RPC call reverted", details={"reason": "RPC_REVERT"}
            )
        return body.get("result")

    def chain_id(self) -> int:
        result = self._jsonrpc("eth_chainId", [])
        return int(str(result), 16)

    def get_code(self, address: str) -> str:
        return str(self._jsonrpc("eth_getCode", [address, "latest"]) or "0x0")

    def get_transaction_count(self, address: str, block: str = "pending") -> int:
        result = self._jsonrpc("eth_getTransactionCount", [address, block])
        return int(str(result), 16)

    def gas_price(self) -> int:
        result = self._jsonrpc("eth_gasPrice", [])
        return int(str(result), 16)


# ---------------------------------------------------------------------------
# Pancake V2/V3 builder
# ---------------------------------------------------------------------------
class PancakeDexBuilder(DexTransactionBuilder):
    name = "PANCAKESWAP"

    def __init__(
        self,
        rpc: EvmRpcClient | None = None,
        *,
        contracts: dict[str, Any] | None = None,
        fee_tiers: tuple[int, ...] = V3_FEE_TIERS,
        default_slippage_bps: int = 100,
    ) -> None:
        self._rpc = rpc or HttpxEvmRpcClient()
        self._contracts = dict(PANCAKE_CONTRACTS)
        if contracts:
            self._contracts.update({k: v for k, v in contracts.items() if v})
        self._fee_tiers = fee_tiers
        self._default_slippage_bps = default_slippage_bps

    @classmethod
    def from_settings(cls, settings_obj: Any | None = None) -> "PancakeDexBuilder":
        """Build from application settings (V4.2D: wires PANCAKE_CONTRACT_ADDRESSES
        so a reviewed contract override is honored instead of silently ignored)."""
        from app.config import get_settings
        import json

        settings = settings_obj or get_settings()
        contracts: dict[str, Any] = {}
        raw = (getattr(settings, "pancake_contract_addresses", "") or "").strip()
        if raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {}
            contracts = {str(k): str(v) for k, v in data.items() if v}
        return cls(HttpxEvmRpcClient(getattr(settings, "bnb_rpc_url", "")), contracts=contracts)

    def is_configured(self) -> bool:
        return self._rpc.is_configured()

    # -- DexTransactionBuilder interface -------------------------------------
    def build_bnb_swap(self, **kwargs: Any) -> DexBuildResult:
        token_in = kwargs.get("token_in") or kwargs.get("input_mint")
        token_out = kwargs.get("token_out") or kwargs.get("output_mint") or kwargs.get("token_address")
        taker = kwargs.get("taker") or kwargs.get("taker_public_key") or kwargs.get("from_address")
        if not token_in or not token_out or not taker:
            raise SettlementDexBuildFailedError(
                "Pancake build requires token_in, token_out and taker",
                details={"reason": "MISSING_REQUIRED_INPUT"},
            )
        amount_raw = kwargs.get("amount_raw")
        if amount_raw is None:
            scale = kwargs.get("scale")
            decimals = int(kwargs.get("input_decimals") or 18)
            amount_raw = int(Decimal(str(scale)) * (10**decimals))
        raw_slip = kwargs.get("slippage_bps")
        slippage_bps = self._default_slippage_bps if raw_slip is None else int(raw_slip)
        deadline = kwargs.get("deadline")
        return self.quote_and_build(
            token_in=token_in,
            token_out=token_out,
            amount_raw=int(amount_raw),
            taker=taker,
            slippage_bps=slippage_bps,
            deadline=deadline,
            recipient=kwargs.get("recipient"),
            nonce=kwargs.get("nonce"),
            gas=kwargs.get("gas"),
            gas_price=kwargs.get("gas_price"),
            chain=kwargs.get("chain"),
        )

    def build_solana_swap(self, **kwargs: Any) -> DexBuildResult:
        raise SettlementDexInvalidResponseError(
            "PancakeSwap is a BNB venue; it cannot build a Solana swap"
        )

    def quote_expected_minimum_out(
        self,
        *,
        token_in: str,
        token_out: str,
        amount_raw: int,
        slippage_bps: int,
    ) -> int | None:
        """READ-ONLY best-route quote (V4.2G hardening): returns the
        slippage-adjusted MINIMUM executable output (``minimum_amount_out_raw``)
        of the SAME reviewed V2/V3 route selection used by execution, or None when
        no trustworthy route is available."""
        if amount_raw <= 0 or not (0 <= int(slippage_bps) < 10000):
            return None
        try:
            route = self._select_best_route(
                token_in, token_out, int(amount_raw), int(slippage_bps)
            )
        except (SettlementDexQuoteUnavailableError, SettlementDexRouteUnavailableError, SettlementDexInvalidResponseError):
            return None
        quote, _venue, _fee = route
        return quote.minimum_amount_out_raw

    # -- quote + build --------------------------------------------------------
    def quote_and_build(
        self,
        *,
        token_in: str,
        token_out: str,
        amount_raw: int,
        taker: str,
        slippage_bps: int | None = None,
        deadline: int | None = None,
        recipient: str | None = None,
        nonce: int | None = None,
        gas: int | None = None,
        gas_price: int | None = None,
        chain: str | None = None,
    ) -> DexBuildResult:
        if not self.is_configured():
            raise SettlementDexQuoteUnavailableError(
                "BNB RPC is not configured", details={"reason": "RPC_NOT_CONFIGURED"}
            )
        # V4.2D chain identity guard: a Solana / Ethereum / wrong-BNB-network
        # request must fail closed BEFORE any signable material is produced.
        if chain is not None and chain != "BNB":
            raise SettlementDexBuildFailedError(
                "PancakeSwap builds are BNB-only; a cross-chain request must fail closed",
                details={"reason": "CHAIN_MISMATCH", "chain": chain},
            )
        configured_chain = int(self._contracts.get("chain_id") or CHAIN_ID_BSC)
        if configured_chain not in (CHAIN_ID_BSC, BSC_TESTNET_CHAIN_ID):
            raise SettlementDexBuildFailedError(
                "PancakeSwap contracts configure an unrecognized chain id; refusing to build",
                details={"reason": "UNRECOGNIZED_CHAIN_ID", "chain_id": configured_chain},
            )
        # Live chain-identity verification: the RPC must agree with the configured
        # contract chain. UNKNOWN (unsupported read / RPC down) fails closed.
        try:
            rpc_chain_id = int(self._rpc.chain_id())
        except SettlementDexQuoteUnavailableError:
            raise SettlementDexBuildFailedError(
                "BNB RPC chain identity could not be verified; refusing to build",
                details={"reason": "CHAIN_IDENTITY_UNKNOWN"},
            )
        if rpc_chain_id != configured_chain:
            from app.providers.settlement_execution.errors import SettlementExecutionNetworkMismatchError

            raise SettlementExecutionNetworkMismatchError(
                "BNB RPC chain id does not match the configured PancakeSwap contracts",
                details={
                    "provider": "PANCAKESWAP",
                    "expected_chain_id": configured_chain,
                    "rpc_chain_id": rpc_chain_id,
                },
            )
        if amount_raw <= 0:
            raise SettlementDexSlippageInvalidError("amount must be positive")
        slip = int(slippage_bps if slippage_bps is not None else self._default_slippage_bps)
        if not (0 <= slip < 10000):
            raise SettlementDexSlippageInvalidError(
                "slippage_bps must be in [0, 10000)", details={"slippage_bps": slip}
            )
        if token_in == BSC_NATIVE or token_out == BSC_NATIVE:
            raise SettlementDexBuildFailedError(
                "native BNB routing is a documented boundary not implemented in this slice",
                details={"reason": "NATIVE_INPUT_NOT_SUPPORTED"},
            )

        route = self._select_best_route(token_in, token_out, amount_raw, slip)
        quote, venue, fee = route

        spender = (
            self._contracts["v2_router"]
            if venue == "PANCAKESWAP_V2"
            else self._contracts["v3_swap_router"]
        )
        approval = self._model_approval(token_in, taker, amount_raw, spender)

        if deadline is None:
            import time

            deadline = int(time.time()) + 600  # 10-minute validity window
        unsigned = self._build_unsigned_evm(
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_raw,
            amount_out_min=quote.minimum_amount_out_raw,
            taker=taker,
            recipient=recipient or taker,
            venue=venue,
            fee=fee,
            deadline=deadline,
            nonce=nonce,
            gas=gas,
            gas_price=gas_price,
        )
        warnings = ["UNSIGNED_ONLY: no signature; external signer boundary required"]
        if approval.status == "INSUFFICIENT":
            warnings.append("ERC-20 approval required before execution")
        elif approval.status == "UNKNOWN":
            warnings.append("allowance unknown; swap must not be treated as executable")
        return DexBuildResult(
            chain="BNB",
            venue=venue,
            unsigned=unsigned,
            unsigned_serialized=unsigned.data,
            quote=quote,
            approval=approval,
            status="AVAILABLE",
            warnings=tuple(warnings),
        )

    # -- route selection ------------------------------------------------------
    def _select_best_route(
        self, token_in: str, token_out: str, amount_raw: int, slippage_bps: int
    ) -> tuple[QuoteEvidence, str, int | None]:
        candidates: list[tuple[QuoteEvidence, str, int | None]] = []
        v2 = self._quote_v2(token_in, token_out, amount_raw, slippage_bps)
        if v2 is not None:
            candidates.append((v2, "PANCAKESWAP_V2", None))
        for fee in self._fee_tiers:
            v3 = self._quote_v3(token_in, token_out, amount_raw, slippage_bps, fee)
            if v3 is not None:
                candidates.append((v3, "PANCAKESWAP_V3", fee))
        if not candidates:
            raise SettlementDexRouteUnavailableError(
                "no valid PancakeSwap route for the requested pair",
                details={"token_in": token_in, "token_out": token_out},
            )
        best = max(candidates, key=lambda c: c[0].expected_amount_out_raw)
        return best

    # -- V2 -------------------------------------------------------------------
    def _quote_v2(
        self, token_in: str, token_out: str, amount_raw: int, slippage_bps: int
    ) -> QuoteEvidence | None:
        factory = self._contracts["v2_factory"]
        try:
            pair_raw = self._rpc.eth_call(
                to=factory,
                data=_encode_call(
                    "getPair(address,address)",
                    ["address", "address"],
                    [_checksum(token_in), _checksum(token_out)],
                ),
            )
            pair = _decode_result(["address"], pair_raw)[0]
        except SettlementDexQuoteUnavailableError:
            return None  # pair lookup failed -> route unavailable (skipped)
        if not pair or pair == "0x" + "00" * 20:
            return None  # no pair deployed
        try:
            token0_raw = self._rpc.eth_call(to=pair, data=_encode_call("token0()", [], []))
            token0 = _decode_result(["address"], token0_raw)[0].lower()
            reserves_raw = self._rpc.eth_call(to=pair, data=_encode_call("getReserves()", [], []))
            reserve0, reserve1, _block_ts = _decode_result(
                ["uint112", "uint112", "uint32"], reserves_raw
            )
        except SettlementDexQuoteUnavailableError:
            return None
        reserve_in, reserve_out = (reserve0, reserve1) if token0 == token_in.lower() else (reserve1, reserve0)
        expected = quote_v2_exact_in(amount_raw, int(reserve_in), int(reserve_out))
        if expected is None:
            return None  # zero liquidity / invalid reserves -> rejected
        return QuoteEvidence(
            chain="BNB",
            venue="PANCAKESWAP_V2",
            input_mint=token_in,
            output_mint=token_out,
            amount_in_raw=amount_raw,
            expected_amount_out_raw=expected,
            minimum_amount_out_raw=slippage_min_out(expected, slippage_bps),
            slippage_bps=slippage_bps,
            liquidity=LiquidityEvidence(
                kind="PAIR_RESERVES",
                units="raw pair reserve units",
                values={"reserve_in_raw": int(reserve_in), "reserve_out_raw": int(reserve_out)},
            ),
            route_legs=(RouteLegEvidence(label="PancakeV2", in_amount_raw=amount_raw, out_amount_raw=expected),),
            validity={},
        )

    # -- V3 -------------------------------------------------------------------
    def _quote_v3(
        self, token_in: str, token_out: str, amount_raw: int, slippage_bps: int, fee: int
    ) -> QuoteEvidence | None:
        path = _pack_v3_path(_checksum(token_in), fee, _checksum(token_out))
        data = _encode_call(
            "quoteExactInput(bytes,uint256)", ["bytes", "uint256"], [path, amount_raw]
        )
        try:
            raw = self._rpc.eth_call(to=self._contracts["v3_quoter_v2"], data=data)
            amount_out, _sqrt, _ticks, _gas = _decode_result(
                ["uint256", "uint160", "uint32", "uint256"], raw
            )
        except SettlementDexQuoteUnavailableError:
            return None  # pool unavailable / revert -> skip this fee tier
        if int(amount_out) <= 0:
            return None
        expected = int(amount_out)
        return QuoteEvidence(
            chain="BNB",
            venue="PANCAKESWAP_V3",
            input_mint=token_in,
            output_mint=token_out,
            amount_in_raw=amount_raw,
            expected_amount_out_raw=expected,
            minimum_amount_out_raw=slippage_min_out(expected, slippage_bps),
            slippage_bps=slippage_bps,
            liquidity=LiquidityEvidence(
                kind="V3_QUOTE",
                units="none",
                values={"fee_tier": fee},
            ),
            route_legs=(RouteLegEvidence(label=f"PancakeV3-{fee}", in_amount_raw=amount_raw, out_amount_raw=expected),),
            validity={},
        )

    # -- approval / unsigned build --------------------------------------------
    def _model_approval(
        self, token_in: str, taker: str, amount_raw: int, spender: str
    ) -> ApprovalRequirement:
        if token_in == BSC_NATIVE:
            return ApprovalRequirement(status="NATIVE_INPUT")
        try:
            allowance_raw = self._rpc.eth_call(
                to=token_in,
                data=_encode_call(
                    "allowance(address,address)",
                    ["address", "address"],
                    [_checksum(taker), _checksum(spender)],
                ),
            )
            allowance = int(_decode_result(["uint256"], allowance_raw)[0])
        except SettlementDexQuoteUnavailableError:
            return ApprovalRequirement(status="UNKNOWN", token=token_in, spender=spender)
        if allowance >= amount_raw:
            return ApprovalRequirement(
                status="SUFFICIENT", token=token_in, spender=spender, current_allowance_raw=allowance
            )
        return ApprovalRequirement(
            status="INSUFFICIENT",
            token=token_in,
            spender=spender,
            required_allowance_raw=amount_raw,
            current_allowance_raw=allowance,
        )

    def _build_unsigned_evm(
        self,
        *,
        token_in: str,
        token_out: str,
        amount_in: int,
        amount_out_min: int,
        taker: str,
        recipient: str,
        venue: str,
        fee: int | None,
        deadline: int,
        nonce: int | None = None,
        gas: int | None = None,
        gas_price: int | None = None,
    ) -> UnsignedEvmTransaction:
        path = [_checksum(token_in), _checksum(token_out)]
        if venue == "PANCAKESWAP_V2":
            router = self._contracts["v2_router"]
            data = _encode_call(
                "swapExactTokensForTokens(uint256,uint256,address[],address,uint256)",
                ["uint256", "uint256", "address[]", "address", "uint256"],
                [amount_in, amount_out_min, path, _checksum(recipient), deadline],
            )
        else:
            router = self._contracts["v3_swap_router"]
            packed_path = _pack_v3_path(_checksum(token_in), int(fee or 500), _checksum(token_out))
            data = _encode_call(
                "exactInput((bytes,address,uint256,uint256,uint256))",
                ["(bytes,address,uint256,uint256,uint256)"],
                [(packed_path, _checksum(recipient), deadline, amount_in, amount_out_min)],
            )
        return UnsignedEvmTransaction(
            chain_id=self._contracts["chain_id"],
            from_address=taker,
            to_address=router,
            data=data,
            value=0,
            deadline=deadline,
            nonce=nonce,
            gas=gas,
            gas_price=gas_price,
            validity={
                "unsigned": True,
                "chain_id": self._contracts["chain_id"],
                "nonce": nonce,
                "gas": gas,
                "gas_price": gas_price,
            },
        )


def _checksum(value: str) -> str:
    """Return the address with a mixed-case checksum via eth_utils (lowercase fallback)."""
    from eth_utils import to_checksum_address

    try:
        return to_checksum_address(value)
    except Exception:
        return value
