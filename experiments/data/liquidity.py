"""Observed Mint/Burn accounting and explicit fixed-token-flow counterfactuals.

Applies matched liquidity events in global log order so counterfactual reserves
follow the recorded absolute token flows.
"""

from types import SimpleNamespace

from eth_utils import keccak

MINT_TOPIC = "0x" + keccak(text="Mint(address,uint256,uint256)").hex()
BURN_TOPIC = "0x" + keccak(text="Burn(address,uint256,uint256,address)").hex()
MODEL = "fixed_observed_token_flows"


def decode_pool_events(events, quote_index, times, traders):
    """Split pool events into swap logs and matched Mint/Burn liquidity events.

    Each Swap/Mint/Burn must be preceded by a Sync in the same transaction;
    Syncs paired with swaps are forwarded to :func:`decode_logs`.

    Args:
        events: Raw pool logs for the interval.
        quote_index: Index (0 or 1) of the quote asset in the pair.
        times: Mapping from block number to Unix timestamp.
        traders: Mapping from transaction hash to initiating address.

    Returns:
        Tuple of the swap :class:`pandas.DataFrame` and the liquidity list.
    """
    from .fetch_pool_history import SWAP_TOPIC, SYNC_TOPIC, decode_logs, words

    pending, swap_logs, liquidity = {}, [], []
    for event in sorted(
        events, key=lambda e: (int(e["blockNumber"], 16), int(e["logIndex"], 16))
    ):
        if event.get("removed"):
            raise ValueError("Removed/reorged pool event")
        topic, tx = event["topics"][0].lower(), event["transactionHash"]
        block = int(event["blockNumber"], 16)
        key = (block, tx)
        if topic == SYNC_TOPIC:
            if key in pending:
                raise ValueError(
                    "Unmatched Sync: no Swap/Mint/Burn consumed the previous Sync"
                )
            pending[key] = event
        elif topic in (SWAP_TOPIC, MINT_TOPIC, BURN_TOPIC):
            if key not in pending:
                raise ValueError("Pool operation is missing its preceding Sync")
            sync = pending.pop(key)
            if topic == SWAP_TOPIC:
                swap_logs.extend([sync, event])
                continue
            amounts, reserves = words(event["data"]), words(sync["data"])
            if len(amounts) != 2 or len(reserves) != 2 or min(amounts) <= 0:
                raise ValueError("Unsupported zero-sided or malformed Mint/Burn")
            liquidity.append(
                dict(
                    block=block,
                    ts=times[block],
                    log_index=int(event["logIndex"], 16),
                    tx_hash=tx,
                    trader=traders[tx].lower(),
                    kind="MINT" if topic == MINT_TOPIC else "BURN",
                    amount_quote_raw=str(amounts[quote_index]),
                    amount_token_raw=str(amounts[1 - quote_index]),
                    reserve_quote_after_raw=str(reserves[quote_index]),
                    reserve_token_after_raw=str(reserves[1 - quote_index]),
                )
            )
    if pending:
        raise ValueError(
            "Unmatched Sync: standalone reserve updates remain unsupported"
        )
    return decode_logs(swap_logs, quote_index, times, traders), liquidity


def ordered_events(meta, swaps):
    """Return swaps and liquidity events merged in global block/log order.

    Args:
        meta: History metadata dict (may include "liquidity_events").
        swaps: Swap rows.

    Returns:
        List of "(block, log_index, is_liquidity, row)" tuples sorted by
        position, where liquidity rows are :class:`types.SimpleNamespace`.
    """
    liquidity = meta.get("liquidity_events", [])
    if liquidity and meta.get("liquidity_model") != MODEL:
        raise ValueError("Missing/unsupported liquidity counterfactual model")
    rows = [(int(r.block), int(r.log_index), False, r) for r in swaps.itertuples()]
    for event in liquidity:
        if event.get("kind") not in ("MINT", "BURN"):
            raise ValueError("Unsupported liquidity event kind")
        for name in (
            "amount_quote_raw",
            "amount_token_raw",
            "reserve_quote_after_raw",
            "reserve_token_after_raw",
        ):
            value = event[name]
            if (
                not isinstance(value, str)
                or not value.isascii()
                or not value.isdigit()
                or int(value) <= 0
            ):
                raise ValueError(
                    "Liquidity raw values must be positive integer decimal strings"
                )
        rows.append(
            (
                int(event["block"]),
                int(event["log_index"]),
                True,
                SimpleNamespace(**event),
            )
        )
    rows.sort(key=lambda r: r[:2])
    if len({r[:2] for r in rows}) != len(rows) or any(r[1] < 0 for r in rows):
        raise ValueError("Duplicate/invalid pool event position")
    return rows


def apply_liquidity(pool, event):
    """Apply fixed raw token flows; never overwrite counterfactual reserves.

    Args:
        pool: The :class:`CpmmPool` whose reserves are updated in place.
        event: Liquidity event with "kind" and raw amount fields.

    Raises:
        ValueError: If the kind is unsupported or the resulting reserves are
            nonpositive or exceed uint112.
    """
    if event.kind not in ("MINT", "BURN"):
        raise ValueError("Unsupported liquidity kind")
    quote, token = int(event.amount_quote_raw), int(event.amount_token_raw)
    if min(quote, token) <= 0:
        raise ValueError("Unsupported zero-sided liquidity flow")
    sign = 1 if event.kind == "MINT" else -1
    rq, rt = (
        pool.reserve_quote_raw + sign * quote,
        pool.reserve_token_raw + sign * token,
    )
    if not (0 < rq < 2**112 and 0 < rt < 2**112):
        raise ValueError(
            f"Infeasible fixed liquidity flow at {event.block}:{event.log_index}; abort this path"
        )
    pool.reserve_quote_raw, pool.reserve_token_raw = rq, rt
