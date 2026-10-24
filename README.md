# Indicators as Constraints

A reproducible experiment harness for *Indicators as Constraints:
Red-Teaming EU Market-Manipulation Indicators on AMM-Based Crypto Markets*.
It simulates fixed-input counterfactuals from historical PancakeSwap V2 swaps, calls a frozen existing
strategy, and measures manufactured-volume costs and MAR (a)–(e) alarm rates.
Historical admission checks the exact fee-adjusted invariant and observed reserve
accounting, separately reporting rounding-compatible and other nonmaximal outputs. Simulation holds observed
inputs fixed; it does not infer router intent or promise identical historical outputs.

## Quick start (no credentials)

Python 3.11 or newer is required; Python 3.12 is the validated environment.

```bash
python -m venv .venv
source .venv/bin/activate
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
python -m pip install -e '.[test]'
python -m pytest -q
redteam demo --output output/demo
```

For the exact validated dependency versions on Python 3.12:

```bash
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .
```

The demo generates three deterministic fixture pools, assigns one to calibration
and two to testing, and runs all 15 configurations spanning M0–M7. The demo uses
six hours of replay, one-hour evaluation windows, one seed, both information
views, both threshold percentiles, both calibration variants, and three risk
weights. It requires no database, node, wallet, API key, or network access.

```bash
redteam demo --output output/demo_sensitivity --sensitivities
# Faster validation of the same configuration grid:
redteam demo --output output/smoke --hours 2 --sensitivities
```

The optional sensitivity suite adds six scenarios: no arbitrage, fees of 10 and
30 bps, half-length windows, and half/double initial pool depth. It recalibrates
on calibration pools for each scenario. Outputs are never overwritten: use a new
output directory for each experiment. Depth and fee changes are counterfactual
simulations, not faithful reconstructions of observed chain state.

## Experimental design

- **M0:** fixed interval, zero predictive trend, one available wallet, fixed
  funding, disabled organic participation input, no self-trade guard.
- **M1:** dynamic interval.
- **M2:** historical sandbox trend supplied to the original direction rule.
- **M3:** multiple wallets, using the original bundle planner.
- **M4:** reproducible random-range funding amounts.
- **M5:** participation caps of 5%, 10%, 20%, and 50%.
- **M6:** 60/300-second guard, branching from M5 at 10%.
- **M7:** the requirements' **S1 inventory-band sweep**, branching from M5 at 10%.
  It does not inherit M6's guard. This mapping was explicitly confirmed by the author.

M0 retains the original strategy's inventory recovery and sizing rules. It is
not guaranteed to alternate in every market state. The strategy's zero-organic-
volume convention disables its cap even at M5 on a completely quiet minute.
These strategy semantics are preserved, not silently corrected.

The strategy implementation is stored under `experiments/vendor/backend/`, and
the executable harness and experiment documentation live under `experiments/`.

## Historical experiments

The 36-pool historical experiment runs through the
[data eligibility and supplementation workflow](experiments/docs/REPLACEMENT_PROTOCOL.md).
`python -m experiments.replacements run` runs that formal pre-experiment stage: it
re-audits the fixed eligibility rule, replaces any unqualified pool from a
deterministic reserve order, re-qualifies and freezes the final 12/24 cohort, then
runs the primary experiment. The original configuration and retained histories are
preserved.

1. Preregister pool selection, the calibration/test split (1:2), volatility
   strata, time periods, targets, starting endowments, and all evaluation choices.
   The checked-in `config/pools.yaml` fixes 36 discovered pools (12 calibration,
   24 test) and the 2026-10-11 through 2026-10-14 UTC interval. These identities
   are fixed. Historical eligibility is established by the qualification audit
   before freezing. See the
   [cohort protocol](experiments/docs/COHORT_PROTOCOL.md) and
   [full address table](experiments/config/study_evidence/cohort.csv).
2. Obtain a frozen **Pinax BSC Parquet export** of logs, transactions, and blocks.
   Follow [Data sources](experiments/docs/DATA_SOURCES.md) for download commands,
   required coverage, native columns, and resource limits. Generate the selected
   cohort's metadata-filled import configurations, then import locally:

   ```bash
   export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
   python -m experiments.data.download_pinax --plan
   python -m experiments.data.download_pinax
   python -m experiments.study --config experiments/config --prepare-imports \
     --output data/pinax_$(date +%Y%m%d)/import_configs
   python -m experiments.data.import_pinax \
     --config data/pinax_$(date +%Y%m%d)/import_configs/pool_01.yaml \
     --output data/pinax_$(date +%Y%m%d)/pool_01
   ```
3. For the supplied cohort, keep the declared identities and splits, import
   histories to `data/pinax_$(date +%Y%m%d)/pool_01` through `pool_36`, then audit:

   ```bash
   python -m experiments.study --config experiments/config \
     --output data/pinax_$(date +%Y%m%d)/qualification
   ```

   A pool that fails the fixed eligibility rule cannot enter the main experiment.
   Complete the cohort through the formal data-eligibility-and-supplementation
   stage ([REPLACEMENT_PROTOCOL.md](experiments/docs/REPLACEMENT_PROTOCOL.md)),
   which deterministically replaces only those vacant slots from a fixed reserve
   order and re-audits before freezing. Thresholds are never relaxed and no
   outcome is consulted. Alternative studies require a new protocol; history
   paths are relative to its configuration directory. Example schema:

   ```yaml
   selection:
     status: frozen
     chain: BSC
     venue: PancakeSwapV2
     daily_volume_usd: [100000, 1000000]  # example only; choose before results
     period: "AUTHOR-PREREGISTERED PERIOD"
     volatility_strata: [low, medium, high]
     split_ratio: [1, 2]
   pools:
     - {id: calibration_01, split: calibration, history: ../../data/pool_01}
     - {id: test_01, split: test, history: ../../data/pool_02}
     - {id: test_02, split: test, history: ../../data/pool_03}
   ```

4. Freeze configuration and data, then run:

   ```bash
   redteam plan --config experiments/config
   redteam freeze --config experiments/config
   redteam run --config experiments/config --output output/pinax_$(date +%Y%m%d)
   ```

A changed configuration, history, or strategy implementation invalidates the lock.
Create a new protocol directory for a revised study. Calibration uses only
organic replay on calibration pools, before any test-pool strategy run. A pool
cannot serve as multiple independent bootstrap clusters.

The primary historical default is 72 hours, 24-hour windows with one-hour
steps, one funding seed, and the unchanged 25-bps PancakeSwap quote function.
Funding amounts do not change strategy decisions or graph topology in this model;
extra funding seeds do not add independent market observations.
Every block is retained, including blocks without swaps. The downloader requires
exact observed reserve accounting. Pinax imports include matched Mint/Burn as
fixed external token flows, applied to simulated reserves in global log order.
They are not trades and do not contribute to manufactured volume. Unexplained
Sync updates, unsupported multi-input swaps and infeasible reserve withdrawals
still fail. The model does not infer LP behavior or reset manipulated reserves to Sync.

## Supplementary experiment: General Player vs Top Player

The primary grid stacks controls cumulatively (M0–M7): every later level inherits
the earlier controls. A threshold-aware trader does not reason that way. Instead
of adding complexity, it tunes the one or two knobs a given MAR statistic is
actually sensitive to. The supplementary experiment answers the follow-up question
the cumulative ladder leaves open — *given the same frozen data, can a smarter
trader dodge the indicators by deviating one factor at a time?*

It reuses the frozen historical cohort and its qualified histories, but replaces
the cumulative `ladder.yaml` with a directed
**Top Player grid** declared under `specs:`. Each spec starts from the honest
baseline `G0` (the primary experiment's M0) and changes a single control toward a
declared MAR statistic, so every effect stays one-factor-at-a-time attributable:

| Spec | Target | Deviation |
| --- | --- | --- |
| `G0` | baseline | Honest M0 baseline |
| `TP_a` | (a) volume share | Small absolute size and bounded organic participation |
| `TP_b` | (b) price impact | Small symmetric round-trip (expected silent) |
| `TP_c` | (c′) net position | One-sided nominal inventory run with a wide band |
| `TP_d` | (d) reversal | Push reversal cadence beyond the 600 s window |
| `TP_e` | (e) burst | Fixed uniform cadence |
| `TP_g1` | G1 linked wallets | Single wallet, no bundle |
| `TP_acd` | a + c′ + d | Combined evasion (trades b-impact against quietness) |

`TP_b` and `TP_c`/`TP_d` pull in opposite directions: a symmetric round-trip keeps
(b) silent while exposing (c′)/(d), whereas a one-sided run dodges (c′)/(d) at the
cost of the price impact (b) detects. No single path silences every wash-trading
statistic at once.

### Cohort amendment

The supplementary configuration was preregistered to reuse the primary
12-calibration / 24-test cohort unchanged. The re-fetched historical snapshot
(`pinax_20261024`) left four pools failing the fixed eligibility rule —
`pool_15` (calibration), `pool_17`, `pool_20` and `pool_33` (test) — each with
fewer than 72 organic swaps. The supplementary protocol was therefore amended,
before any intervention outcome, to a **10-calibration / 20-test cohort (30
pools)** without invoking the reserve-replacement stage. The four failed pools
were dropped, and two further pools — `pool_14` (calibration) and `pool_24`
(test), both pre-flagged as replacement vacancies in the primary design — were
dropped to preserve the preregistered 1:2 calibration:test ratio. No eligibility
threshold was relaxed and no outcome-driven exclusion was applied; the retained
identities and splits are fixed in `config_supplementary/pools.yaml`.

Freeze and run the grid, then contrast it against the completed main experiment:

```bash
# Historical qualification audit for the amended 30-pool cohort
python -m experiments.study --config experiments/config_supplementary \
  --output data/pinax_20261024/qualification

python -m experiments.run freeze --config experiments/config_supplementary
python -m experiments.run run --config experiments/config_supplementary \
  --output output/supplementary --jobs 15
python -m experiments.analysis.supplementary --main output/pinax36 \
  --supplementary output/supplementary --config experiments/config_supplementary \
  --jobs 15
```

The primary grid runs **8 specs × 20 test pools = 160 intervention paths**.
Adding `--sensitivities` runs the full seven-scenario grid (8 × 20 × 7 = 1,120
paths); the configuration raises `max_runs` accordingly. The comparison writes
three artifacts into `output/supplementary`:

- `supplementary_alarm_comparison.csv` — per-indicator General Player (M0 and
  in-grid minimum) versus targeted Top Player alarm rates.
- `supplementary_evasion_comparison.csv` — per-indicator evasion classification
  contrast between the cumulative and directed grids.
- `supplementary_report.json` — the summary record with the ε, V* and δ settings.

## Outputs

The full list of generated artifacts and their meaning is documented in
[Outputs](experiments/docs/OUTPUTS.md). The paper-facing visual encodings, panel
semantics and manuscript-use gates are fixed in the
[figure specification](experiments/docs/FIGURES.md).

### Regenerate figures without replaying the experiment

A completed experiment contains everything needed to rebuild the paper figures
and derived reporting tables. Write them to a new directory so the immutable
experiment manifest and its hashes remain valid:

```bash
redteam plot --input output/completed_run --output output/completed_run_plots
```

The input must contain a final `manifest.json`, `protocol.lock.json`,
`summary.csv`, `thresholds.csv`, `evasion.csv`, `frozen.yaml`, and every referenced
run's `indicator_windows.parquet` and `trades.parquet`. Runs marked
`INCOMPLETE.json` are rejected. The plot directory must be new or empty and gets
its own `plot_manifest.json` binding the figures to the source experiment.

## Development

```bash
python -m pytest -q
python -m ruff check experiments
python -m ruff format --check experiments
```

Tests forbid socket connections and synchronous/asynchronous HTTP requests.
The downloader is the only network-using component; it allows only read methods.
The strategy preview, bundle planner, and guard never reach a settlement service.
The tests include integer pricing, analytical bounds, exact replay, FIFO reversal,
entity attribution, calibration separation, missing evidence, and full ladder
execution.

## Resource and free-data policy

The selected study budget is 45 GiB of virtual address space, 64 GiB of output,
50 hours of wall time, and 360 strategy runs. Memory is capped by POSIX `RLIMIT_AS`; time,
disk, and indicator rows are checked at processing boundaries. These are
admission/checkpoint limits, not a prediction that a historical study will finish
within 50 hours. An interrupted run contains `INCOMPLETE.json` and must not be
used as evidence. Limits are frozen in `frozen.yaml`.

`redteam plan` inspects Parquet metadata without running strategies. A primary
24-test-pool study needs 360 paths; the full seven-scenario grid needs 2,520
and is intentionally rejected by the default budget. Profile calibration data
before preregistration, then explicitly budget the full study. Do not prune
settings or pools based on test results. Risk-weight sensitivities reuse paths;
indicator reversion uses a linear-time algorithm, and pools are processed
serially by default. Logs use deterministic gzip compression.

All randomness is fixed by the single seed 42, so every run is reproducible. The
simulation funding seed is `seeds: [42]` in `frozen.yaml`, and
`entities.funding_edges` draws from `numpy.random.default_rng(seed)`; bootstrap
confidence intervals use `bootstrap_seed: 42`; and the cohort selection and
calibration/test stratification derive from the fixed `SEED` in `experiments/study.py`
(`42`) hashed with each pool pair. `protocol.py` rejects empty,
duplicate, or non-integer seeds before any run.

Pool execution can be parallelized across worker processes without changing
results. `--jobs N` (default `1`, serial) runs the calibration and test pools
concurrently with N workers; results are collected in the same order, so output
determinism is unchanged. The workload is GIL-bound Python, so use multiple
processes rather than threads:

```bash
redteam run --config experiments/config --output output/pinax_$(date +%Y%m%d) --jobs 8
```

Each worker inherits the wall-time budget from the coordinator and enforces its
own memory and row limits; the shared output disk budget remains a coordinator
check. `--jobs` also applies to `redteam demo`,
`python -m experiments.replacements run`, and
`python -m experiments.analysis.supplementary`.

Path B uses Pinax archives and optional offline quote-price CSVs; import and
experiments make no network requests and have no RPC fallback. The public Pinax
demo does not guarantee archive coverage or permanent availability. Every block
is required, so fast-block periods can exceed the default row budget. See
[study planning](experiments/docs/STUDY_PLAN.md) for a bounded workflow.

## Availability and interpretation

The repository is released under the MIT license. Code comments are preserved
as written, and all new code, docs, logs, charts, and command-line interfaces
are in English.

Software validation does not establish the paper's hypotheses, historical-data
coverage, regulatory conclusions, or publication acceptance.

## One-shot reproduction of the 36-pool historical experiment

Copy-paste the commands below to reproduce the full pipeline on a fresh
checkout. Steps 5a–5d stage the pre-experiment workflow so each checkpoint can
be inspected and resumed independently; `python -m experiments.replacements run`
alone also replays prepare → complete → run in one shot.

```bash
# 0. Enter the repository and pull the latest code (replace with the actual path)
cd /path/to/QuietPool
git pull --ff-only origin main

# 1. Environment (skip the venv-creation line if .venv already exists)
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1

# 2. Regenerate the cohort (deterministic, seed 42)
python -m experiments.data.generate_cohort --config experiments/config

# 3. Verify the code (proceed only once these all pass)
python -m pytest -q
python -m ruff check experiments
python -m ruff format --check experiments

# 4. Regenerate configs/pools and import the data
# Generate the 36 import configs
python -m experiments.study --config experiments/config \
  --prepare-imports --output data/pinax_$(date +%Y%m%d)/import_configs
# Download the 36-pool raw data (~9.5 GiB)
python -m experiments.data.download_pinax \
  --config experiments/config --output data/pinax_$(date +%Y%m%d)/raw
# Import pool_01..pool_36 one by one
for i in $(seq 1 36); do
  id=$(printf "pool_%02d" "$i")
  python -m experiments.data.import_pinax \
    --config "data/pinax_$(date +%Y%m%d)/import_configs/${id}.yaml" \
    --output "data/pinax_$(date +%Y%m%d)/${id}"
done

# 5. Run the full pipeline in stages (each step is independently inspectable
#    and can be resumed from a checkpoint after a failure)
# 5a. prepare: data eligibility check — verify the original 36-pool histories
#     and fix the reserve candidate order (no network, seconds)
python -u -m experiments.replacements prepare \
  --work data/pinax_$(date +%Y%m%d)/replacement
# 5b. plan: read remote metadata only, estimate download traffic (no actual download)
python -u -m experiments.replacements plan \
  --work data/pinax_$(date +%Y%m%d)/replacement
# 5c. complete: acquire data → import → supplement (deterministic-order screening)
#     → final eligibility check → freeze protocol
#     (~10 GiB download)
python -u -m experiments.replacements complete \
  --work data/pinax_$(date +%Y%m%d)/replacement
# 5d. run: primary experiment (360 intervention paths, up to 50-hour wall clock)
#     --jobs 16 = 16 parallel worker processes (GIL-bound, use processes not threads;
#     results remain deterministic)
python -u -m experiments.replacements run \
  --work data/pinax_$(date +%Y%m%d)/replacement \
  --output output/pinax36 \
  --jobs 16
```
