# Data eligibility and supplementation

Data eligibility and supplementation is the formal, pre-experiment stage that turns
the fixed 36-pool discovery cohort into the qualified 12-calibration / 24-test
cohort used by the main experiment. It runs entirely before any strategy outcome
is produced and has three ordered sub-steps:

1. **Eligibility audit.** Re-run the shared historical eligibility rule
   (`qualify_pool`) on all 36 selected histories and record every pass or failure
   together with its reason. The rule requires at least 72 organic swaps and
   positive finite baseline volatility, on top of the importer's Sync/swap/reserve
   checks.
2. **Supplementation.** Replace any pool that fails that rule with the first
   eligible reserve drawn from a pre-fixed, hash-ordered inventory of the
   unselected discovery candidates, using the *same* rule. Thresholds are never
   relaxed and no intervention outcome is consulted.
3. **Final re-audit.** Re-audit the amended 36 pools and the unchanged baseline
   volatility-coverage gate, then freeze only if everything passes.

## Why this stage exists

The discovery filters (venue, volume, liquidity) select candidates before their
historical replay has been inspected. Some selected pools therefore turn out to be
historically unqualified, each with too few organic swaps or non-positive/
non-finite baseline volatility. Such a pool cannot enter the main experiment,
because its baseline would be unreliable and it could not contribute a valid
volatility observation to the fixed 12/24 design. Rather than silently dropping
the pool (which would break the fixed design), relaxing the threshold (which would
bias eligibility), or picking replacements to obtain favorable results (which is
forbidden), the stage replaces only the vacant slots from a deterministic reserve
list. Every retained pool and exclusion is preserved and reported.

The original 36 histories import, then the baseline rule (at least 72 organic
swaps and positive finite volatility) is applied. Supplementation retains every
pool that passes and replaces only the vacant slots, keeping 12 calibration and
24 test pools, the same 72-hour interval, the same strategy grid and all existing
eligibility thresholds. This is a documented amendment after baseline inspection,
not original preregistration.

## Run the complete pipeline

From the installed repository, with the original 36 imported histories present:

```bash
git pull --ff-only origin main
source .venv/bin/activate
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
mkdir -p logs
RUN_TAG=$(date -u +%Y%m%dT%H%M%SZ)
set -o pipefail
python -u -m experiments.replacements run \
  --work data/pinax_$(date +%Y%m%d)/replacement \
  --output "output/pinax36_${RUN_TAG}" \
  2>&1 | tee "logs/pinax36_${RUN_TAG}.log"
```

This performs preparation, acquisition, screening, final qualification, resource
planning, protocol freezing and the full primary experiment. It uses the existing
report generator for tables and PDF/SVG/PNG figures. It does not launch the optional
seven-scenario sensitivity grid, which exceeds the primary 360-path budget.

For staged inspection, the same CLI also accepts `prepare`, `plan`, and `complete`.
`plan` reads remote metadata only; `complete` stops after qualification/freezing.
All commands use the same `--work` default shown above. After `complete` succeeds,
the existing commands also work:

```bash
redteam plan --config data/pinax_$(date +%Y%m%d)/replacement/final_config
redteam run --config data/pinax_$(date +%Y%m%d)/replacement/final_config \
  --output output/pinax36
```

After that run completes, figures and derived reporting tables can be rebuilt
without repeating acquisition, qualification or the strategy grid:

```bash
redteam plot --input output/pinax36 --output output/pinax36_plots
```

The destination must be new or empty. Preserve its `plot_manifest.json` alongside
the source experiment manifest when supplying paper evidence.

## Deterministic supplementation rule

1. Re-audit the original histories. Stop if the failed identities/reasons differ
   from the five authorized low-observation exclusions. Hash all original
   histories, including excluded ones. Do not modify the original configuration.
2. Reproduce the original discovery filters and asset deduplication. The frozen
   discovery inventory supplies 21 unselected candidates. Sort them by
   SHA256(`42|reserve_replacement_first_eligible_fixed_order|PAIR`),
   breaking ties by pair address. Persist this order and its input hashes before
   downloading candidate events. Discovery activity is not historical eligibility.
3. Acquire all candidate pool projections in one bounded pass over the same
   frozen Pinax objects. Assign acquisition-only IDs pool_37 through pool_57.
   Downloading these together avoids scanning whole remote partitions once per pool.
4. Import and qualify candidates in that fixed order using the same importer and
   shared `qualify_pool` rule as the original cohort. Select the first five eligible
   pools; stop importing when five qualify. Keep all rejections and mark remaining
   candidates as not evaluated. Resource or availability failures are not grounds
   for silently skipping a candidate. Exhaustion stops the workflow.
5. Assign these five, in order, to slots pool_14, pool_15, pool_20, pool_24,
   pool_34. Preserve each slot's original split. Retain the 31 original addresses
   and histories. Slot IDs in results refer to the amended mapping; consult
   `amendment_manifest.json` for original and replacement addresses.
6. Re-audit all 36 histories and unchanged baseline volatility-coverage thresholds.
   There is no automatic reshuffling or further replacement to obtain favorable
   strata or intervention outcomes. Freeze only if qualification and the resource
   plan pass. Insufficient eligible reserves or stratum coverage can still block
   execution and are reported rather than hidden.

No strategy outcome is consulted during replacement. Retain the exclusions and
failed candidate list in the paper's sample-construction account. Baseline-driven
selection limits generalization to sufficiently active pools; 36 pools is not
itself a statistical-power or publication guarantee.

## Resources and recovery

Retained histories are referenced, not copied. The original download
contains only its selected pools, so candidate acquisition requires additional
remote column reads: plan for roughly the prior 10.4 GiB projection, with a
16 GiB cumulative payload ceiling and an 8 GiB acquisition-directory disk cap.
This is a planning estimate, not measured candidate download volume.
Downloader defaults remain 4 GiB memory and two hours per invocation; explicitly
set `--download-seconds` if needed. Screening has a 4 GiB memory, 16 GiB work-folder
disk and one-hour cooperative wall cap. Each candidate import retains its own
ten-minute/512 MiB output limits. Formal experiment limits remain 45 GiB memory,
64 GiB result disk, 360 intervention paths and **180,000 seconds (50 hours)**.
Preparation/download time is separate from the experiment limit. Limits abort;
they do not promise completion or bound the machine's total disk consumption.

Keep the original data and the amendment work folder. Interrupted downloads resume
verified ranges; successful candidate imports are reused and requalified. Source,
plan, import-configuration or amendment changes fail explicitly. Run only one
pipeline per work folder. A partially prepared work folder without a complete plan
must be inspected; use a fresh work folder for a revised protocol.
Formal strategy runs are not checkpoint-resumable: inspect partial output and
choose a new `--output` for a new run. Existing result directories are never reused.

## Audit artifacts

| Artifact under the work folder | Meaning |
| --- | --- |
| `original_qualification/` | Original 36-pool baseline audit and exclusions |
| `replacement_plan.json`, `.sha256` | Fixed candidate order and bound source/history/config hashes |
| `reserve_config/`, `import_configs/` | Acquisition and import definitions |
| `candidates/raw/acquisition.json` | Completed candidate acquisition and byte hashes |
| `candidates/pool_*/` | Successfully imported candidate histories |
| `screening.csv/.json` | Ordered decisions, reasons, selected slots and history hashes |
| `amendment_manifest.json` | Address replacements, unchanged split counts and evidence hashes |
| `final_config/` | Independent final 36-pool configuration and protocol lock |
| `qualification/` | Final eligibility and volatility strata |
| `execution_plan.json` | Actual resource admission plan before experiment execution |

Amendment evidence is hash-checked during protocol freeze and subsequent runs.
