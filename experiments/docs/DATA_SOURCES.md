# Data sources and Pinax download guide

Path A (`redteam demo`) uses deterministic synthetic fixtures to validate software.
Path B uses **Pinax BSC logs + transactions + blocks**, then runs entirely offline.
Neither import nor evaluation contacts a BSC node, exchange, or trading service.
`fetch_pool_history.py` is a legacy opt-in utility, not the official Path B and
never an automatic fallback.

## Availability and downloading

### Automatic acquisition for the fixed 36-pool cohort

Run from the repository root after installing the project (Python 3.11+):

```bash
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
# Only inspect Parquet metadata and estimate traffic:
python -m experiments.data.download_pinax --plan
# Download, filter, and write the complete raw directory:
python -m experiments.data.download_pinax
```

The second command creates `data/pinax_$(date +%Y%m%d)/raw/blocks.parquet`,
`bnb_usd.csv`, and `pool_01` through `pool_36`, each containing `logs.parquet`
and `transactions.parquet`: 74 data files plus provenance and resume metadata.
It reads the frozen active-object inventory in
`config/study_evidence/pinax_snapshot.json`; no RPC, AWS CLI, API key, or running
BSC node is needed. Public archive availability is still required.

The 2026-10-22 metadata-only check inspected 133 relevant objects: 30.64 GiB
of full objects, 9.45 GiB of projected column chunks, and a conservative
10.41 GiB traffic estimate including headroom. These are planning estimates,
not a completed historical download or measured final disk size. HTTP ranges
fetch column chunks for relevant row groups; server-side per-pool filtering is
not assumed. Pool filtering happens locally, so network traffic exceeds output
size. Row groups are processed sequentially.

Defaults cap cumulative downloaded payload at 16 GiB, output/cache disk at
8 GiB, process memory at 4 GiB, acquisition time at 7,200 seconds per invocation,
and selected rows at two million per table. Limits abort rather than guarantee
completion. Acquisition is separate from the experiment's 50-hour limit.
At an effective 20 Mbps, 10.41 GiB alone takes about 75 minutes; at 100 Mbps,
about 15 minutes, before processing and request overhead. Final disk consumption
and historical eligibility must be measured, not inferred from synthetic tests.

After interruption, rerun the same command: verified ranges and completed
object extractions are reused. Traffic accounting persists across invocations.
Do not run two download processes against the same output directory. If a
resource limit was reached, inspect the plan/partial output before explicitly
raising that limit, for example `--wall-seconds 36000`. Changed input identities,
ETags, corrupt caches, missing objects, ignored Range requests, and schema drift
fail explicitly. A reset demo snapshot is not silently replaced.

Prices come from official Binance public daily BNBUSDT one-minute archives,
with archive SHA256 verification. The tool uses minute **opens**, converts
microsecond timestamps, and checks causal coverage with a 60-second maximum
age. `BNBUSDT` approximates USD under the documented **USDT=USD assumption**;
it is not an independent BNB/USD feed. If those archives are unavailable, supply
an audited `ts,quote_usd` CSV using `--prices-file /absolute/path/prices.csv`
on both plan and download commands in a fresh `--output` directory.

`acquisition.json` is written only after all 74 files pass acquisition checks;
it records source ETags, metadata hashes, price provenance, and output SHA256s.
Generated cohort import configurations require this manifest and reject changed
inputs. It does not prove source completeness or replace the importer's parent
chain, Sync/Swap, reserve, and pool qualification checks. Then prepare and import:

```bash
python -m experiments.study --config experiments/config --prepare-imports \
  --output data/pinax_$(date +%Y%m%d)/import_configs
for n in $(seq -w 1 36); do
  python -m experiments.data.import_pinax \
    --config "data/pinax_$(date +%Y%m%d)/import_configs/pool_${n}.yaml" \
    --output "data/pinax_$(date +%Y%m%d)/pool_${n}" || break
done
python -m experiments.study --config experiments/config \
  --output data/pinax_$(date +%Y%m%d)/qualification
```

Import outputs must be new directories; after a stopped import, inspect its
partial output before retrying. Qualification failure is resolved by the formal
data-eligibility-and-supplementation stage
([REPLACEMENT_PROTOCOL.md](REPLACEMENT_PROTOCOL.md)): the fixed rule decides
eligibility and a deterministic reserve fills only vacant slots. This is not
automatic, outcome-driven pool substitution.

If an import reports `Unmatched Sync`, do not delete or redownload the raw data.
Current imports support matched Mint/Burn. A Sync not paired with a
Swap can arise from Mint/Burn, a standalone sync, or incomplete/misordered input;
the exception alone does not identify the cause. Diagnose all pools offline:

```bash
python -m experiments.data.audit_pinax_events \
  --output "output/event_audit_$(date -u +%Y%m%dT%H%M%SZ)"
```

`event_summary.csv` counts Sync/Swap/Mint/Burn and pairing issues per pool;
`event_issues.csv` lists affected blocks, global log indices, transaction hashes,
and same-transaction Mint/Burn evidence. The tool verifies raw log hashes against
the acquisition record, filters the declared interval, and records missing or
changed inputs as `audit_error`. It does not certify eligibility or validate raw
reserve transitions. Limits are 4 GiB memory, 128 MiB report disk, 30 minutes,
and two million raw rows per pool. No chain queries or strategy runs occur.

The swap-pairing diagnostic alone cannot qualify Mint/Burn. Status
`liquidity_events_require_import` directs these pools to the full importer,
which pairs each operation with its preceding same-transaction Sync and verifies
exact amounts, global order and reserve changes.
Do not skip Syncs, reset manipulated reserves to historical values, or replace
pools based on desired experimental outcomes.

### Liquidity model amendment: fixed observed token flows

Matched Mint/Burn are retained in `meta.json` as `liquidity_events`, with raw
amounts stored as decimal strings, global log positions and transaction origins.
`liquidity_model: fixed_observed_token_flows` identifies the model. These data
are covered by the existing history/protocol hashes; the import manifest records
their count and model. The amendment replaces the original swap-only restriction.
Existing swap-only imports remain compatible and need not be regenerated.

Historical qualification applies each recorded addition/withdrawal and checks its
Sync exactly before processing subsequent swaps. Simulation applies the same
absolute token amounts to current counterfactual reserves, in global log order.
Liquidity does not enter trade counts, manufactured volume, participation-volume
denominators or swap-fee costs. Price/wealth marks and arbitrage reference update
through the existing block-level mechanism. Baseline volatility includes ordered
Mint/Burn reserve-price observations as well as swaps; the minimum organic-swap
count for study eligibility remains unchanged. A flow producing nonpositive or
out-of-uint112 reserves aborts the path; it is never clipped or silently dropped.
Depth sensitivities change starting reserves but keep these exogenous flows fixed.

This is an external-flow scenario, not an exact LP-share execution model. It does
not reconstruct LP balances/supply, router min-amount constraints, liquidity-provider
responses, or common ownership between liquidity providers and the manipulator.
Under altered reserves a fixed two-token Burn need not equal a proportional LP
redemption. State this limitation in the manuscript; do not claim that historical
event accounting proves behavioral fidelity of the intervention experiment.
Standalone Sync, missing pairs, malformed/zero-sided Mint/Burn and unexplained
reserve changes remain unsupported. Gas and liquidity-provider P&L are not added
to the manipulator's F/S/R cost definition.

### Other snapshots and manual exports

Start at [Pinax BSC](https://data.pinax.network/config/?bucket=bsc) and its
[schema browser](https://data.pinax.network/schemas/). At implementation time,
the public read-only S3-compatible endpoint was
`https://parquet.riv-dev1.pinax.io`, region `auto`, bucket `bsc`. Anonymous bucket
listing was verified. This is a **demo**, with changeable schemas and resettable
coverage; historical periods, free selective exports, uptime, and redistribution
rights are not guaranteed. Check current coverage and provider terms before
preregistering the study. No account or paid API is required for the public
objects currently exposed; do not assume this applies to every Pinax product.

Inspect sizes first using the optional AWS CLI (not a runtime dependency):

```bash
aws --endpoint-url https://parquet.riv-dev1.pinax.io --region auto \
  s3api list-objects-v2 --bucket bsc --prefix 'blocks/date=YYYY-MM-DD/' \
  --max-items 10 --no-sign-request
```

Repeat for `logs/date=.../` and `transactions/date=.../`, substituting a date
actually listed by the provider. This lists metadata, not the entire dataset.
The bucket uses **Delta tables**: directory listing is not an active snapshot
manifest. Compaction can leave obsolete Parquet files. Obtain an active
file list using a Delta-aware client or request a consistent export from Pinax.
Do not glob all objects or download
`_delta_log` checkpoints as event tables. A Delta client is only needed during
acquisition; the importer accepts explicit local Parquet files.

For an approved active object, check its size and download it explicitly:

```bash
aws --endpoint-url https://parquet.riv-dev1.pinax.io --region auto \
  s3api head-object --bucket bsc --key 'TABLE/date=DATE/EXACT_FILE.parquet' \
  --no-sign-request
mkdir -p data/pinax
curl --fail --location --max-time 300 --max-filesize 536870912 \
  'https://parquet.riv-dev1.pinax.io/bsc/TABLE/date=DATE/EXACT_FILE.parquet' \
  --output data/pinax/EXACT_FILE.parquet.part
# Only after successful completion:
mv data/pinax/EXACT_FILE.parquet.part data/pinax/EXACT_FILE.parquet
sha256sum data/pinax/EXACT_FILE.parquet
```

These are templates, not known file keys. The example caps one download at
512 MiB and five minutes. Enforce a cumulative download budget as well. If an
object exceeds the cap, obtain a filtered export; avoid blindly raising limits.
Full BSC log partitions can be gigabytes per day. The importer does not make
whole-chain acquisition economical. Availability of a free subset must be
confirmed with the current provider. A locally generated SHA256 identifies bytes;
it does not authenticate them unless compared to a trusted provider checksum.

## Required export contract

Use canonical snapshot rows, not fork-update streams. Retain native columns:

| Table | Required columns |
| --- | --- |
| All | `block_num`, `block_id`, `timestamp` (Arrow timestamp with timezone) |
| blocks | `parent_id`, `lib_num` |
| transactions | `hash`, `index`, `from` |
| logs | `address`, `tx_hash`, `tx_index`, `block_index`, `topic0`, `data` |

Hex strings with or without `0x`, or binary bytes, are supported. Amounts are
decoded directly from ABI words without floats. **`block_index` is the global
log order; `log_index` is transaction-relative and must not be substituted.**
Schema reference: [Pinax firehose-parquet EVM schema](https://github.com/pinax-network/firehose-parquet).
Actual public Parquet footers for all three tables were also checked using
bounded range reads; their native fields matched this contract (timestamps used
microseconds). Schema drift fails explicitly; renamed columns must be reviewed,
not guessed.

1. Export all logs for the pair from `coverage_start` through `end_block`,
   including a Sync strictly before `start_block`. Do not select only Swap logs.
   The latest preceding Sync provides the initial reserves. Record an assertion
   that this is a complete export, including the range and filtering query.
2. Export transactions referenced by in-range pool Sync/Swap logs. Attribution
   uses transaction `from`, never the router's event sender or recipient.
3. Export **every block**, including empty ones, from `coverage_start` through a
   later witness block with `lib_num >= end_block`. Keep the intervening witness
   chain too. Only start-to-end blocks enter the simulation. Missing LIB evidence
   is a hard failure; no RPC fallback is attempted.
4. Supply audited pair/token addresses, decimals, quote side, and evidence of
   PancakeSwap V2 membership. These cannot be inferred reliably from these three
   tables alone. Archive the metadata source and date; never guess token decimals.
5. For WBNB or another quote asset, supply local `ts,quote_usd` CSV prices known
   at each timestamp. Backward joins reject missing/stale prices; default maximum
   age is 60 seconds. Hourly opens require an explicit 3600-second age and a
   documented approximation. Do not timestamp closes at their opening time.
   USDT=USD is allowed only by explicit configuration and remains an assumption.

Pair-filtered logs and referenced transactions may be much smaller than whole
partitions. Header coverage must remain complete. When exporting from a larger
local snapshot, use predicate pushdown and bounded batches; preserve the snapshot
IDs, source file manifest, and export query alongside the acquisition record.

## Offline import and experiment

Copy `experiments/config/pinax.example.yaml` to `pinax.local.yaml` in the same
directory, fill its nulls and explicit file lists, and set quote-price policy.
All file paths are relative to the YAML. `snapshot` and `metadata_source` must
contain meaningful provenance; the software cannot verify those statements.
Do not put credentials in configuration because it is persisted in the manifest.

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m experiments.data.import_pinax \
  --config experiments/config/pinax.local.yaml --output data/pool_01
```

Default admission caps are 2 GiB of source files and two million input rows;
processing uses projected columns and 2,048-row batches, a 2 GiB memory budget,
512 MiB output budget and ten-minute cooperative time budget. POSIX also applies
an address-space cap; Windows checks peak RSS cooperatively. A single blocking
file read/hash is not forcibly interrupted by the time checker. These are limits,
not runtime guarantees. The row cap counts source rows before filtering. A raw
log object may exceed it even for a quiet pair; obtain a subset or explicitly
budget a larger source scan after a calibration pilot. Block/pair filtering is
performed in Arrow before Python row materialization. Selected headers are also
capped by `max_blocks_per_pool` (400,000 by default); selected pool events by
four times `max_swaps_per_pool` (200,000 by default). Use a fresh output directory
for every import.

Outputs are `meta.json`, `swaps.parquet`, `blocks.parquet`, and
`import_manifest.json`. The manifest records source SHA256/size, configuration,
anchor and finality witness; the experiment protocol freezes all four artifacts.
Add imported histories to the calibration/test pool configuration, then use the
README's `redteam plan`, `freeze`, and `run` workflow. Existing cost tables,
MAR a–e alarm rates, M0–M7 grid, and PDF/SVG/PNG figures consume this same format.

## Scientific limits and manuscript changes

Historical swap qualification requires positive one-sided input/output, valid
reserve bounds, the exact 25-bps fee-adjusted invariant, and exact observed
reserve transitions, without a float tolerance. The pair contract permits outputs
below `getAmountOut`: maximal router output is not a contract requirement. The
manifest separately counts maximal exact-input outputs, additional integer
`getAmountIn`-compatible outputs, and other invariant-compatible nonmaximal outputs.
It rejects outputs above the legal maximum and all unexplained reserve changes.
An output matching another fee formula does not establish a different contract
fee; neither router method, intent nor the cause of under-output is inferred.

The organic counterfactual continues to hold observed inputs fixed and recompute
outputs at simulated reserves. It does not reproduce exact-output intent or
guarantee bit-for-bit historical outputs even without intervention: compatible
rounding and nonmaximal-output differences can propagate. In particular, simulation
does not preserve voluntary under-output or router-specific conservative quotes.
The manifest's nonmaximal count is a model-mismatch disclosure, not proof that
the resulting bias is negligible. Historical accounting qualification and this
behavioral simulation assumption must be distinguished in manuscript claims.

Parent hashes, tx/log joins, exact baseline output and reserve transitions reject
many corrupt/incomplete exports, but **cannot prove absence of omitted logs** or
independently validate consensus. Receipt proofs and an authenticated chain
checkpoint are not supplied. The importer trusts Pinax's canonical snapshot and
LIB reporting. Whole transactions/pool events that cancel state changes can be
omitted without a detectable reserve discrepancy. Retain the acquisition audit.

Qualification excludes unexplained reserve changes and unsupported flash/multi-input
swaps. Event accounting alone does not establish absence of transfer taxes outside
the pool. Record exclusions and avoid selecting pools
by desired results. Historical transaction.from is an initiating address, not
verified beneficial ownership. Organic treasury links are not reconstructed from
these exports; controlled-wallet funding remains synthetic. Gas-adjusted economic
claims would need receipt gas usage/effective prices and an explicit cost-model
extension. The existing F/S/R cost definition does not silently include gas.

Suggested manuscript data description: “We replay canonical Pinax BSC archives
of pool logs, referenced transactions, and contiguous block headers. Initial
reserves come from the latest pre-interval Sync. We retain transaction-origin
attribution and global event ordering, verify swap invariants and exact Swap/Mint/Burn
reserve changes, and freeze source hashes and acquisition metadata before
calibration/testing. Intervention simulations fix observed swap inputs and external
liquidity token flows.”
Also disclose provider completeness trust, USD assumptions, exclusions, finite
strategy-grid inference and the historical reserve-ratio arbitrage reference.
Synthetic verification is not empirical support for the hypotheses. Real frozen
pool splits, sufficiently many independent pools, and completed historical runs
are still necessary before claiming publication-level empirical evidence.

Completed experiment outputs are also sufficient for offline figure regeneration:

```bash
redteam plot --input output/completed_run --output output/completed_run_plots
```

This command performs no Pinax, Binance or RPC access. It reads only the completed
result directory, rejects `INCOMPLETE.json`, and records source hashes in
`plot_manifest.json`.
