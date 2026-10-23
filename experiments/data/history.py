"""Lossless history storage and strict swap-only replay qualification.

Preserves uint112/uint256 amounts as decimal strings and validates that
observed swaps reproduce the exact 25-bps constant-product invariant.
"""

import json
import re
from pathlib import Path

import pandas as pd

from ..sandbox.cpmm import CpmmPool
from .liquidity import apply_liquidity, ordered_events

RAW_COLUMNS = [
    "amount_in_raw",
    "amount_out_raw",
    "reserve_quote_after_raw",
    "reserve_token_after_raw",
]


def write_frame(frame, path, budget=None):
    """Write a frame to Parquet, stringifying raw amounts for lossless storage.

    Args:
        frame: :class:`pandas.DataFrame` to write.
        path: Destination Parquet path.
        budget: Optional admission budget to reserve against.
    """
    if budget:
        budget.reserve(int(frame.memory_usage(deep=True).sum() * 2) + 65536)
    frame = frame.copy()
    # uint112/uint256 exceed signed int64; decimal strings are lossless in Parquet.
    for column in frame:
        if column.endswith("_raw"):
            frame[column] = frame[column].map(str)
    frame.to_parquet(path, index=False)


def save_history(folder, meta, swaps, blocks):
    """Persist a qualified history as "meta.json" plus swap/block Parquet.

    Args:
        folder: Output directory (created if necessary).
        meta: History metadata dict written to "meta.json".
        swaps: Swap rows written to "swaps.parquet".
        blocks: Block rows written to "blocks.parquet".
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    write_frame(swaps, folder / "swaps.parquet")
    blocks.to_parquet(folder / "blocks.parquet", index=False)


def load_history(folder):
    """Load and validate a saved history from "folder".

    Args:
        folder: Directory containing "meta.json", "swaps.parquet", and
            "blocks.parquet".

    Returns:
        Tuple of "(meta, swaps, blocks)" with raw amounts converted to int.

    Raises:
        ValueError: If the import is incomplete, the manifest is missing, or
            raw amounts are not exact integer decimal strings.
    """
    folder = Path(folder)
    if (folder / "INCOMPLETE.json").exists():
        raise ValueError("Incomplete history import is not a valid experiment input")
    meta = json.loads((folder / "meta.json").read_text())
    if (
        meta.get("provider") == "Pinax"
        and not (folder / "import_manifest.json").is_file()
    ):
        raise ValueError("Pinax history requires its import manifest")
    swaps = pd.read_parquet(folder / "swaps.parquet")
    blocks = pd.read_parquet(folder / "blocks.parquet")
    for c in RAW_COLUMNS:
        if (
            not swaps[c]
            .map(
                lambda x: (
                    isinstance(x, (str, int))
                    and not isinstance(x, bool)
                    and bool(re.fullmatch(r"[0-9]+", str(x)))
                )
            )
            .all()
        ):
            raise ValueError(
                "Raw amounts must be exact integer decimal strings, never floats"
            )
        swaps[c] = swaps[c].map(int)
    validate_history(meta, swaps, blocks)
    return meta, swaps, blocks


def validate_history(meta, swaps, blocks, *, return_report=False):
    """Validate ordering, continuity, timestamps, and price conversions.

    Args:
        meta: History metadata dict.
        swaps: Swap rows.
        blocks: Block rows.
        return_report: If "True", return the swap-quote fidelity report.

    Returns:
        The quote-fidelity report when "return_report" is "True", else
        "True".
    """
    if blocks.empty or swaps.empty:
        raise ValueError("History must contain blocks and swaps")
    if (
        blocks.block.duplicated().any()
        or not blocks.block.is_monotonic_increasing
        or not blocks.ts.is_monotonic_increasing
    ):
        raise ValueError("Blocks must be ordered and unique")
    if (blocks.block.diff().dropna() != 1).any():
        raise ValueError("All intervening blocks, including empty blocks, are required")
    if swaps.duplicated(["block", "log_index"]).any() or list(
        zip(swaps.block, swaps.log_index, strict=False)
    ) != sorted(zip(swaps.block, swaps.log_index, strict=False)):
        raise ValueError("Swap ordering or duplicate log error")
    stamps = dict(zip(blocks.block, blocks.ts, strict=False))
    if any(stamps.get(r.block) != r.ts for r in swaps.itertuples()):
        raise ValueError("Swap timestamp does not match its block")
    prices = dict(zip(blocks.block, blocks.quote_usd, strict=True))
    if any(stamps.get(e["block"]) != e["ts"] for e in meta.get("liquidity_events", [])):
        raise ValueError("Liquidity timestamp does not match its block")
    if any(prices.get(r.block) != r.quote_usd for r in swaps.itertuples()):
        raise ValueError("Swap USD conversion does not match its block")
    if (blocks.quote_usd <= 0).any() or not blocks.quote_usd.map(
        lambda x: 0 < float(x) < float("inf")
    ).all():
        raise ValueError("Invalid USD conversion")
    report = validate_swap_quotes(meta, swaps)
    return report if return_report else True


def validate_swap_quotes(meta, swaps):
    """Check observed swap accounting; does not validate headers or provenance.

    Args:
        meta: History metadata dict with initial reserves and decimals.
        swaps: Swap rows with raw amounts and post-swap reserves.

    Returns:
        Dict counting exact-input, output-compatible, and nonmaximal swaps.
    """
    pool = CpmmPool(
        int(meta["reserve_quote_raw"]),
        int(meta["reserve_token_raw"]),
        meta["quote_decimals"],
        meta["token_decimals"],
    )
    report = dict(
        exact_input_matches=0, exact_output_compatible=0, invariant_compatible_nonmax=0
    )
    for _, _, is_liquidity, r in ordered_events(meta, swaps):
        if is_liquidity:
            apply_liquidity(pool, r)
            if (pool.reserve_quote_raw, pool.reserve_token_raw) != (
                int(r.reserve_quote_after_raw),
                int(r.reserve_token_after_raw),
            ):
                raise ValueError(
                    f"Liquidity Sync fidelity failure at {r.block}:{r.log_index}"
                )
            continue
        amount_in, amount_out = int(r.amount_in_raw), int(r.amount_out_raw)
        maximum = pool.quote(r.side, amount_in)
        ri, ro = (
            (pool.reserve_quote_raw, pool.reserve_token_raw)
            if r.side == "BUY"
            else (pool.reserve_token_raw, pool.reserve_quote_raw)
        )
        # Router getAmountIn uses floor + 1. This is an exact integer
        # compatibility test, not evidence of the caller's execution intent.
        inverse = (
            ri * amount_out * 10000 // ((ro - amount_out) * 9975) + 1
            if 0 < amount_out < ro
            else None
        )
        if (
            amount_in <= 0
            or amount_out <= 0
            or maximum is None
            or amount_out > maximum
            or amount_out >= ro
            or ri + amount_in >= 2**112
            or (ri * 10000 + amount_in * 9975) * (ro - amount_out) < ri * ro * 10000
        ):
            raise ValueError(
                f"Nonstandard swap at {r.block}:{r.log_index}; "
                f"input={amount_in}, observed_output={amount_out}, "
                f"maximum_output={maximum}, inverse_input={inverse}; "
                "violates supported 25-bps swap bounds/invariant; cause is not inferred"
            )
        report[
            "exact_input_matches"
            if amount_out == maximum
            else "exact_output_compatible"
            if amount_in == inverse
            else "invariant_compatible_nonmax"
        ] += 1
        # Historical qualification follows observed amounts exactly. Simulation
        # separately holds observed inputs fixed and requotes counterfactuals.
        if r.side == "BUY":
            pool.reserve_quote_raw += amount_in
            pool.reserve_token_raw -= amount_out
        else:
            pool.reserve_token_raw += amount_in
            pool.reserve_quote_raw -= amount_out
        for got, expected in [
            (pool.reserve_quote_raw, int(r.reserve_quote_after_raw)),
            (pool.reserve_token_raw, int(r.reserve_token_after_raw)),
        ]:
            if got != expected:
                raise ValueError(f"Sync fidelity failure at {r.block}:{r.log_index}")
    return report
