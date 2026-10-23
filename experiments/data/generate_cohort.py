"""Regenerate the frozen 36-pool cohort from the reproducible discovery snapshot.

Rebuilds "config/pools.yaml", "cohort.csv", and "discovery_audit.csv" so
pool identities, splits, and the reserve list always match "select_cohort"
under the fixed seed "42".
"""

import argparse
import re
from pathlib import Path

import pandas as pd
import yaml

from .. import ROOT, pinax_date
from ..protocol import sha256
from ..study import END, START, USDT, select_cohort

# pools.yaml orders strata alphabetically (high < low < medium); within each
# stratum the four calibration pools precede the eight test pools, preserving
# select_cohort's rank("split") order.
STRATUM_ORDER = ("high", "low", "medium")
CALIBRATION_INDEXES = (0, 3, 6, 9)
TEST_INDEXES = (1, 2, 4, 5, 7, 8, 10, 11)

COHORT_COLUMNS = [
    "id",
    "label",
    "split",
    "pair",
    "token0",
    "token1",
    "quote_index",
    "quote_symbol",
    "quote_decimals",
    "token_decimals",
    "discovery_activity_stratum",
    "historical_volatility_stratum",
    "start_block",
    "end_block",
    "history",
]
AUDIT_COLUMNS = [
    "pair",
    "token",
    "quote",
    "symbol",
    "decimals",
    "dex",
    "created_ms",
    "discovery_volume_usd",
    "discovery_liquidity_usd",
    "discovery_decision",
    "discovery_stratum",
    "split",
]


def _relabel_date(text, date):
    """Rewrite "pinax_<8-digit-date>" segments in "text" to "date"."""
    return re.sub(r"pinax_\d{8}", f"pinax_{date}", text)


def build_pools(discovery, date):
    """Build the declared 36-pool list from :func:`select_cohort` output.

    Args:
        discovery: Path to the discovery snapshot CSV.
        date: Target "YYYYMMDD" suffix for the "history" paths.

    Returns:
        Tuple of the ordered pool dicts and the discovery audit rows.
    """
    selected, audit = select_cohort(discovery)
    by_stratum = {"low": [], "medium": [], "high": []}
    for row in selected:
        by_stratum[row["discovery_stratum"]].append(row)

    pools = []
    index = 1
    for stratum in STRATUM_ORDER:
        group = by_stratum[stratum]
        ordered = [group[i] for i in CALIBRATION_INDEXES] + [
            group[i] for i in TEST_INDEXES
        ]
        for row in ordered:
            token0, token1 = sorted([row["token"], row["quote"]])
            pools.append(
                dict(
                    id=f"pool_{index:02}",
                    label=row["symbol"],
                    split=row["split"],
                    pair=row["pair"],
                    token0=token0,
                    token1=token1,
                    quote_index=int(token1 == row["quote"]),
                    quote_symbol="USDT" if row["quote"] == USDT else "WBNB",
                    quote_decimals=18,
                    token_decimals=int(row["decimals"]),
                    discovery_activity_stratum=row["discovery_stratum"],
                    historical_volatility_stratum="pending_baseline_audit",
                    start_block=START,
                    end_block=END,
                    history=f"../../data/pinax_{date}/pool_{index:02}",
                )
            )
            index += 1
    return pools, audit


def main():
    """Regenerate "pools.yaml", "cohort.csv", and "discovery_audit.csv"."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ROOT / "config"))
    parser.add_argument("--date", default=pinax_date(), help="目标 YYYYMMDD 后缀")
    args = parser.parse_args()
    config = Path(args.config)
    discovery = config / "study_evidence/discovery.csv"

    pools, audit = build_pools(discovery, args.date)

    source = yaml.safe_load((config / "pools.yaml").read_text())
    selection = source["selection"]
    selection["discovery_sha256"] = sha256(discovery)
    if "qualification_report" in selection:
        selection["qualification_report"] = _relabel_date(
            selection["qualification_report"], args.date
        )

    frame = pd.DataFrame(pools, columns=COHORT_COLUMNS)
    (config / "study_evidence/cohort.csv").write_text(frame.to_csv(index=False))

    audit_frame = pd.DataFrame(audit, columns=AUDIT_COLUMNS).fillna("")
    (config / "study_evidence/discovery_audit.csv").write_text(
        audit_frame.to_csv(index=False)
    )

    (config / "pools.yaml").write_text(
        yaml.safe_dump(dict(selection=selection, pools=pools), sort_keys=False)
    )
    print(f"已按 seed=42 重新生成 {len(pools)} 个主池 cohort。")
    print("验证：python -m pytest experiments/tests/test_study.py experiments/tests/test_replacements.py")


if __name__ == "__main__":
    main()
