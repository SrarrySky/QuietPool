# Fixed cohort and historical study protocol

## Decision and qualification

The planned primary cohort is **36 distinct assets/pools: 12 calibration and
24 test**, with the complete addresses, token order, decimals and assignments in
[`config/pools.yaml`](../config/pools.yaml) and
[`cohort.csv`](../config/study_evidence/cohort.csv). Identities and split assignments
are fixed before intervention outcomes. The historical qualification audit must
pass before `redteam freeze` can create an executable protocol lock.
`selection.status: frozen` fixes the selection decision; the qualification report
binds each history hash to the declared configuration.

This is a retrospective study of surviving, publicly indexed pools. It is not a
representative probability sample of all BSC pools that existed during the replay
period. The discovery covariates were observed on 2026-10-21, after the historical
period. This introduces survivor/availability selection and must be disclosed.
Do not call the discovery volume filter a historical-volume filter or claim
prospective out-of-time prediction. Publication suitability depends on the
qualified data and results, not on a configured sample size alone.

## Exact period and block bounds

All times are UTC. Bounds were obtained from active-file statistics in frozen
Pinax Delta checkpoints, not estimated from average block time.

| Purpose | UTC interval | Inclusive block bounds |
| --- | --- | --- |
| Reserve-anchor lookback | 2026-10-10 00:00 to 2026-10-11 00:00 exclusive | 126728031–126919957 |
| Primary replay | 2026-10-11 00:00 to 2026-10-14 00:00 exclusive | 126919958–127494945 |
| End-time guard | 2026-10-14 00:00 | 127494946 |

Import through guard block **127494946**. The simulator excludes blocks at the
72-hour endpoint, but requires the guard to establish the full horizon. There
are **574,989 imported replay/guard headers**, of which 574,988 precede the guard.
The importer additionally needs the lookback headers and a contiguous later LIB
witness. Export a small suffix after the guard until `lib_num >= 127494946`;
do not invent a witness height. A last Sync before the start must exist within
the declared lookback; otherwise the import fails rather than fabricating reserves.

The 72-hour horizon produces 49 overlapping 24-hour sliding windows at one-hour
steps and three fixed 24-hour windows. Overlapping windows and strategy settings
do not create independent pool observations.

Pinned checkpoints: **blocks 13533; logs 13535; transactions 13533**. The
[`pinax_snapshot.json`](../config/study_evidence/pinax_snapshot.json) records active
file keys, sizes, block/timestamp ranges and checkpoint SHA256. Exact block hashes
and log/transaction joins must still agree. Metadata establishes advertised coverage,
not full historical
replay validity or authenticated event completeness. Retain these snapshots
before the public demo resets them.

## Discovery and deterministic assignment

The reproducible discovery snapshot contains 505 indexed pairs. Its provenance
is in [`sources.json`](../config/study_evidence/sources.json); all decisions are in
[`discovery_audit.csv`](../config/study_evidence/discovery_audit.csv).

1. Start with BSC token addresses/decimals from the PancakeSwap extended token
   list. Derive their USDT and WBNB V2 pairs using factory
   `0xca143ce32fe78f1f7019d7d551a6402fc5350c73` and its published V2 init-code hash.
   Query the derived addresses in batches from DexScreener and retain every
   returned address in the discovery snapshot. Derivation is tested against the
   known CAKE/WBNB V2 pair. Indexing is not proof of historical model compliance.
2. Require reported creation no later than 2026-10-03, discovery-time trailing
   daily volume **$1,000–$10,000,000**, and discovery-time liquidity **at least
   $10,000**. These broad limits preserve low-activity pools while excluding
   near-empty metadata records. They do not describe measured replay-period volume.
3. Apply the explicit pegged/rebasing/tax-risk symbol exclusions in `study.py`.
   The exclusion list is conservative, not a claim that all remaining assets
   are tax-free. Exact historical replay remains the decisive compatibility test.
4. Retain one pool per token address and case-insensitive symbol, preferring
   USDT to reduce external-price acquisition. This prevents the same recognized
   asset or wrapper symbol appearing in both calibration and testing. Unrecognized
   common ownership, shared underlyings and market factors remain limitations.
5. Sort the remaining candidates by discovery volume and divide into three
   **activity**, not volatility, strata. Within each, select 12 using SHA256 of
   the fixed seed and pair address, then independently hash-order the split:
   four calibration and eight test. Seed: `42`.

Every primary pool is listed in `cohort.csv`; unselected candidates are recorded
as reserves. Replacement is not automatic and is never driven by outcomes: it is
handled by the formal data-eligibility-and-supplementation stage
([REPLACEMENT_PROTOCOL.md](REPLACEMENT_PROTOCOL.md)), which substitutes only pools
that fail the fixed historical eligibility rule with the first eligible reserve in
a pre-fixed order, then re-audits the amended cohort. A failure that this fixed
stage cannot complete requires a documented new design before intervention
outcomes, with all previous exclusions retained. Never retry different seeds to
obtain a more favorable result.

## Calibration, H3 and inference

The fixed 12 calibration pools alone determine 95th/99th indicator thresholds
and the existing raw/clean calibration variants. All 24 test pools receive all
15 M0–M7 parameter settings: **360 primary intervention paths**. M7 remains the
S1 inventory-band sweep from M5. Funding seed 42 avoids redundant deterministic
replays; extra seeds are not extra independent market samples.

Volatility is measured from unmanipulated historical reserve-ratio minute closes
using the engine's per-sqrt-second definition. Before strategy execution, the
baseline audit forms actual volatility terciles over the 36 pools and records
their calibration/test counts. It requires distinct quantile boundaries and at
least two calibration plus four test pools in each tercile. It never substitutes
discovery price change or activity for volatility, and never moves fixed test
identities to improve balance. At least 72 observed swaps and positive finite
baseline volatility are required per pool. Historical volume is reported even
when it differs from discovery volume; it is not silently used to select winners.

H3 should primarily assess continuous volatility against identified timing-evasion
costs; terciles are descriptive diagnostics. The existing plot's test-sample
terciles may differ from the all-pool baseline audit. Report both definitions.
Unreached targets, infinite finite-grid costs, ties and missing values remain
explicit. Do not replace them with zero or drop them from the study flow diagram.

Use pool-cluster bootstrap intervals (5,000 resamples), paired comparisons within
pools, and disclose common-period cross-pool dependence. Pool bootstrap inference
is conditional on this shared period and assumes sufficiently exchangeable pools;
it does not identify across-regime uncertainty. A later independently chosen
period is a replication, not additional independent pools. With 24 test pools,
an idealized two-sided Fisher-z calculation gives roughly 80% power only for a
correlation around 0.55 at alpha 0.05. Missing/infinite costs and dependence reduce
that power. This is a planning approximation, not demonstrated power for H3.
Do not claim that 36 pools by itself ensures a top-tier publication.

The supplementary experiment (General Player vs Top Player) reuses the frozen
cohort and its qualified histories, but under an amended **10-calibration /
20-test cohort (30 pools)**. The re-fetched historical snapshot
(`pinax_20261024`) left four pools failing the fixed eligibility rule
(`pool_15`, `pool_17`, `pool_20`, `pool_33`, each with fewer than 72 organic
swaps); rather than invoking the reserve-replacement stage, those four pools were
dropped together with two pre-flagged vacancy pools (`pool_14`, `pool_24`) to
preserve the 1:2 calibration:test ratio. It substitutes only the strategy grid in
`ladder.yaml` with the directed `specs` grid; thresholds, calibration pools and
the 72-hour period are otherwise unchanged. See
[METHODOLOGY.md](METHODOLOGY.md) for the General/Top Player contrast.

## Acquisition and resource feasibility

The complete active date partitions for October 10–14 total approximately
**38.7 GiB** across the three tables. **Do not download all of them.** Their keys
are an audit inventory, not a bulk download instruction. Obtain a projection
containing the selected pool logs, referenced transactions and shared contiguous
headers. `python -m experiments.data.download_pinax --plan` estimates range-read
traffic; the same command without `--plan` automatically builds the raw directory.
The measured column projection is 9.45 GiB (10.41 GiB with planning headroom).
Filtering is local and does not require a selective-export service. A completed
historical acquisition and qualification remain prerequisites, not solved
empirical results. See [DATA_SOURCES.md](DATA_SOURCES.md).

Generate the metadata-filled import YAMLs locally:

```bash
python -m experiments.study --config experiments/config \
  --prepare-imports --output data/pinax_$(date +%Y%m%d)/import_configs
```

Put shared headers in `data/pinax_$(date +%Y%m%d)/raw/blocks.parquet`, per-pool logs and
transactions under `raw/pool_01/` through `raw/pool_36/`, and causal minute
BNB/USD observations in `raw/bnb_usd.csv`. USDT-quoted pools use the explicit
USDT=USD assumption. The generated YAMLs retain a 60-second causal-price age
limit. The downloader obtains checksum-verified BNBUSDT minute opens from Binance
public archives, explicitly approximating USD with USDT. An audited local price
CSV can be supplied instead. Generated imports verify `raw/acquisition.json`.

```bash
python -m experiments.data.import_pinax \
  --config data/pinax_$(date +%Y%m%d)/import_configs/pool_01.yaml \
  --output data/pinax_$(date +%Y%m%d)/pool_01
# Repeat for each declared pool, retaining all qualification failures.
python -m experiments.study --config experiments/config \
  --output data/pinax_$(date +%Y%m%d)/qualification
redteam plan --config experiments/config
redteam freeze --config experiments/config
redteam run --config experiments/config --output output/pinax_$(date +%Y%m%d)
```

The full 72-hour primary grid implies about **207 million intervention block
iterations**, plus calibration and organic-only baselines. It is not comparable
to the two-hour synthetic demo's runtime. The selected ceilings are 45 GiB memory,
64 GiB output, 50 hours, 360 intervention paths and 800,000 headers per history;
these are abort limits, not a promise that the study fits. First profile a
calibration-only pilot and budget the full run from measured throughput. Preserve
the 72-hour horizon and all empty blocks. If the budget is insufficient, document
a resource-only protocol change before running the test grid, rather than
dropping expensive settings or truncating histories. The seven-scenario grid is
2,520 paths and intentionally fails the primary path-count limit.

## Qualification and empirical claims

All 36 histories must pass integer-quote/Sync qualification and the actual
volatility-coverage audit. The audit binds every history hash to the declared
configuration; changing histories or assignments invalidates freezing. Pinax
completeness, token metadata, pricing, fixed external liquidity flows and finite-grid
limitations are disclosed in the paper. A successful code audit and a fixed cohort are
necessary preparation; neither is evidence that H1–H3 hold.

Once the historical run is complete, `redteam plot --input COMPLETED_RUN --output
NEW_PLOT_DIRECTORY` may regenerate figures and reporting tables from the bound
outputs. It does not rerun qualification, calibration, strategies or indicators,
and therefore cannot turn an incomplete or unqualified cohort into valid evidence.
