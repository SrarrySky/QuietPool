"""Regression for the observed pool_03 integer quote discrepancy."""

import pandas as pd
import pytest

from experiments.data.history import validate_history
from experiments.sandbox.cpmm import CpmmPool


def history(side="BUY"):
    ri, ro = 334067986128693993382, 88027651243504508784722913
    amount, out = 2236161600054559, 587756505981726021810
    rq, rt = (ri, ro) if side == "BUY" else (ro, ri)
    meta = dict(
        reserve_quote_raw=str(rq),
        reserve_token_raw=str(rt),
        quote_decimals=18,
        token_decimals=18,
    )
    rq, rt = (rq + amount, rt - out) if side == "BUY" else (rq - out, rt + amount)
    first = dict(
        block=1,
        ts=100,
        log_index=46,
        trader="origin",
        quote_usd=1.0,
        side=side,
        amount_in_raw=amount,
        amount_out_raw=out,
        reserve_quote_after_raw=rq,
        reserve_token_after_raw=rt,
    )
    # A subsequent trade must start from actual historical reserves, not the
    # maximal-output state (which differs by 107 raw output units).
    pool = CpmmPool(rq, rt)
    following = pool.swap_exact_in(
        side=side, amount_in_raw=10**18, ts=101, actor="organic", wallet="origin"
    )
    second = dict(
        first,
        block=2,
        ts=101,
        amount_in_raw=10**18,
        amount_out_raw=following.amount_out_raw,
        reserve_quote_after_raw=pool.reserve_quote_raw,
        reserve_token_after_raw=pool.reserve_token_raw,
    )
    return (
        meta,
        pd.DataFrame([first, second]),
        pd.DataFrame(dict(block=[1, 2], ts=[100, 101], quote_usd=[1.0, 1.0])),
    )


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_exact_output_compatible_integer_rounding(side):
    meta, swaps, blocks = history(side)
    report = validate_history(meta, swaps, blocks, return_report=True)
    assert report == dict(
        exact_input_matches=1, exact_output_compatible=1, invariant_compatible_nonmax=0
    )


@pytest.mark.parametrize(
    "side,ri,ro,amount,out",
    [
        (
            "BUY",
            330918000635134002209,
            88936036879925532593730145,
            3756715960041240,
            1007001662136373842256,
        ),
        (
            "SELL",
            89707407892705982388028951,
            328154005915355509489,
            1169160659233794630781,
            4263959892628374,
        ),
    ],
)
def test_observed_nonmaximal_outputs_and_invariant_boundary(side, ri, ro, amount, out):
    meta, swaps, blocks = history(side)
    rq, rt = (ri, ro) if side == "BUY" else (ro, ri)
    meta.update(reserve_quote_raw=str(rq), reserve_token_raw=str(rt))
    swaps = swaps.iloc[:1].astype(object).copy()
    swaps.at[0, "amount_in_raw"] = amount
    swaps.at[0, "amount_out_raw"] = out
    swaps.at[0, "reserve_quote_after_raw"] = rq + amount if side == "BUY" else rq - out
    swaps.at[0, "reserve_token_after_raw"] = rt - out if side == "BUY" else rt + amount
    report = validate_history(meta, swaps, blocks.iloc[:1], return_report=True)
    assert report["invariant_compatible_nonmax"] == 1
    # Exact accounting alone is insufficient: even one unit above the legal
    # maximum must fail, with the corresponding recorded reserves adjusted.
    impossible = amount * 9975 * ro // (ri * 10000 + amount * 9975) + 1
    swaps.at[0, "amount_out_raw"] = impossible
    swaps.at[0, "reserve_quote_after_raw"] = (
        rq + amount if side == "BUY" else rq - impossible
    )
    swaps.at[0, "reserve_token_after_raw"] = (
        rt - impossible if side == "BUY" else rt + amount
    )
    with pytest.raises(ValueError, match="Nonstandard"):
        validate_history(meta, swaps, blocks.iloc[:1])


@pytest.mark.parametrize(
    "change", ["over_output", "under_output", "reserve_unit", "excess_input"]
)
def test_rejects_inconsistent_quotes_and_one_unit_reserve_error(change):
    meta, swaps, blocks = history()
    if change == "over_output":
        swaps.at[0, "amount_out_raw"] += 108
    elif change == "under_output":
        swaps.at[0, "amount_out_raw"] -= 10**9
    elif change == "reserve_unit":
        swaps.at[0, "reserve_token_after_raw"] += 1
    else:
        swaps.at[0, "amount_in_raw"] += 1
    with pytest.raises(ValueError, match="Nonstandard|Sync fidelity"):
        validate_history(meta, swaps, blocks)
