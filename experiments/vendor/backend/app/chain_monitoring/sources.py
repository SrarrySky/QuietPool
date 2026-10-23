"""Read-only chain RPC sources for Monitoring on-chain monitoring.


These sources perform read-only JSON-RPC calls only (no signing, no broadcast,
no funds). A transport is injected so tests can use a fixture transport without
any network access or credentials.
"""


from __future__ import annotations


import itertools
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SOLANA = "SOLANA"
BNB = "BNB"


class RpcError(Exception):
    def __init__(self, method: str, code: int | None, message: str) -> None:
        self.method = method
        self.code = code
        self.message = message
        super().__init__(f"RPC {method} error [{code}]: {message}")


def safe_endpoint_label(url: str) -> str:
    """Redact query params that look like credentials/tokens from a URL."""
    parts = urlsplit(url)
    query = [
        (key, "***" if "key" in key.lower() or "token" in key.lower() else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, safe="*"), parts.fragment))


class RpcTransport(Protocol):
    def post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class HttpxRpcTransport:
    def __init__(self, timeout: float = 10.0) -> None:
        import httpx


        self._client = httpx.Client(timeout=timeout)


    def post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(url, json=payload)
        response.raise_for_status()
        return response.json()


    def close(self) -> None:
        self._client.close()


class ChainRpcSource:
    """Base read-only JSON-RPC source over HTTP."""


    chain: str = ""


    def __init__(
        self,
        http_url: str,
        *,
        timeout: float = 10.0,
        transport: RpcTransport | None = None,
    ) -> None:
        self.http_url = http_url
        self.endpoint_label = safe_endpoint_label(http_url)
        self._transport = transport or HttpxRpcTransport(timeout=timeout)
        self._ids = itertools.count(1)


    def _call(self, method: str, params: list[Any]) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": method,
            "params": params,
        }
        data = self._transport.post(self.http_url, payload)
        if not isinstance(data, dict):
            raise RpcError(method, None, "invalid response")
        if "error" in data:
            error = data["error"] or {}
            raise RpcError(method, error.get("code"), error.get("message", "error"))
        if "result" not in data:
            raise RpcError(method, None, "missing result")
        return data["result"]


    def check_capabilities(self) -> dict[str, Any]:
        raise NotImplementedError


    def close(self) -> None:
        close = getattr(self._transport, "close", None)
        if close is not None:
            close()


class SolanaRpcSource(ChainRpcSource):
    chain = SOLANA


    def check_capabilities(self) -> dict[str, Any]:
        capabilities: dict[str, Any] = {
            "endpoint": self.endpoint_label,
            "chain": self.chain,
            "get_version": False,
            "get_slot": False,
            "get_signatures_for_address": False,
            "get_transaction": False,
        }
        try:
            self._call("getVersion", [])
            capabilities["get_version"] = True
        except Exception:
            pass
        try:
            self._call("getSlot", [{"commitment": "finalized"}])
            capabilities["get_slot"] = True
        except Exception:
            pass
        try:
            self._call("getSignaturesForAddress", ["11111111111111111111111111111111", {"limit": 1}])
            capabilities["get_signatures_for_address"] = True
        except Exception:
            pass
        try:
            self._call(
                "getTransaction",
                ["1111111111111111111111111111111111111111111111111111111111111111", {"encoding": "jsonParsed"}],
            )
            capabilities["get_transaction"] = True
        except Exception:
            pass
        return capabilities


    def get_token_accounts_by_owner(self, owner: str, mint: str) -> list[str]:
        result = self._call(
            "getTokenAccountsByOwner",
            [owner, {"mint": mint}, {"commitment": "finalized", "encoding": "jsonParsed"}],
        )
        values = result.get("value") if isinstance(result, dict) else None
        if not isinstance(values, list):
            raise RpcError("getTokenAccountsByOwner", None, "missing value list")
        return sorted(
            item["pubkey"]
            for item in values
            if isinstance(item, dict) and isinstance(item.get("pubkey"), str)
        )


    def get_signatures_for_address(
        self, address: str, *, before: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        config: dict[str, Any] = {"commitment": "finalized", "limit": limit}
        if before is not None:
            config["before"] = before
        result = self._call("getSignaturesForAddress", [address, config])
        if not isinstance(result, list):
            raise RpcError("getSignaturesForAddress", None, "missing result list")
        return result


    def get_transaction(self, signature: str) -> dict[str, Any] | None:
        return self._call(
            "getTransaction",
            [
                signature,
                {
                    "commitment": "finalized",
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
    def get_token_account_balance(self, token_account: str) -> dict[str, Any]:
        result = self._call(
            "getTokenAccountBalance",
            [token_account, {"commitment": "finalized"}],
        )
        if not isinstance(result, dict) or not isinstance(result.get("value"), dict):
            raise RpcError("getTokenAccountBalance", None, "missing value")
        return result
    def finalized_slot(self) -> int:
        return int(self._call("getSlot", [{"commitment": "finalized"}]))
    def genesis_hash(self) -> str:
        value = self._call("getGenesisHash", [])
        if not isinstance(value, str) or not value:
            raise RpcError("getGenesisHash", None, "missing hash")
        return value

class BnbRpcSource(ChainRpcSource):
    chain = BNB


    def check_capabilities(self) -> dict[str, Any]:
        capabilities: dict[str, Any] = {
            "endpoint": self.endpoint_label,
            "chain": self.chain,
            "chain_id": None,
            "latest_block": False,
            "get_transaction_receipt": False,
        }
        try:
            capabilities["chain_id"] = self._call("eth_chainId", [])
        except Exception:
            pass
        try:
            self._call("eth_getBlockByNumber", ["latest", False])
            capabilities["latest_block"] = True
        except Exception:
            pass
        try:
            self._call("eth_getTransactionReceipt", ["0x" + "0" * 64])
            capabilities["get_transaction_receipt"] = True
        except Exception:
            pass
        return capabilities


    def get_transaction_receipt(self, tx_hash: str) -> dict[str, Any] | None:
        return self._call("eth_getTransactionReceipt", [tx_hash])


    def get_transaction_by_hash(self, tx_hash: str) -> dict[str, Any] | None:
        return self._call("eth_getTransactionByHash", [tx_hash])


    def get_block(self, block: str | int) -> dict[str, Any] | None:
        block_tag = hex(block) if isinstance(block, int) else block
        return self._call("eth_getBlockByNumber", [block_tag, False])


    def get_logs(
        self, *, from_block: int, to_block: int, addresses: list[str]
    ) -> list[dict[str, Any]]:
        filter_payload: dict[str, Any] = {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
        }
        if addresses:
            filter_payload["address"] = addresses
        result = self._call("eth_getLogs", [filter_payload])
        if not isinstance(result, list):
            raise RpcError("eth_getLogs", None, "missing result list")
        return result
    def historical_call(self, *, to: str, data: str, block: int) -> str:
        return self._call("eth_call", [{"to": to, "data": data}, hex(block)])


    def finalized_head(self) -> dict[str, Any] | None:
        return self.get_block("finalized")


    def receipt_is_finalized(self, receipt: dict[str, Any] | None) -> bool:
        """True when a receipt's block is at or below the finalized head."""
        if not receipt or receipt.get("blockNumber") is None:
            return False
        head = self.finalized_head()
        if head is None or head.get("number") is None:
            return False
        try:
            return int(receipt["blockNumber"], 16) <= int(head["number"], 16)
        except (ValueError, TypeError):
            return False


# --- WebSocket subscription builders (pure, no connection required) ----------


def solana_subscription_requests(addresses: list[str]) -> list[dict[str, Any]]:
    """Build the ``logsSubscribe`` / ``accountSubscribe`` requests for the
    monitored addresses, mirroring the frozen Monitoring subscription shape."""
    requests: list[dict[str, Any]] = []
    request_id = 1
    for address in addresses:
        requests.append(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "logsSubscribe",
                "params": [{"mentions": [address]}, {"commitment": "finalized"}],
            }
        )
        request_id += 1
    for address in addresses[1:]:
        requests.append(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "accountSubscribe",
                "params": [address, {"commitment": "finalized", "encoding": "jsonParsed"}],
            }
        )
        request_id += 1
    return requests


def bnb_subscription_request(addresses: list[str], *, request_id: int = 1) -> dict[str, Any]:
    """Build the ``eth_subscribe`` logs request for the monitored log addresses."""
    if not addresses:
        raise ValueError("at least one log address is required")
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "eth_subscribe",
        "params": ["logs", {"address": addresses}],
    }
